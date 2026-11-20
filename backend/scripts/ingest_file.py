"""Command-line ingestion: create (or reuse) a quiz and build its question bank from a file.

    cd backend && .venv/bin/python -m scripts.ingest_file ../samples/out/Report.docx --title "Q3 Review"

Handy for trying a report without going through the admin screens.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
from ingest import MIME_TYPES, extension_of  # noqa: E402
from ingest.pipeline import process_document  # noqa: E402
from storage import storage  # noqa: E402


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "quiz"


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file")
    parser.add_argument("--title", default=None)
    parser.add_argument("--slug", default=None)
    parser.add_argument("--show", type=int, default=0, help="print this many questions afterwards")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    path = Path(args.file)
    await db.init_pool()
    await db.run_migrations()

    title = args.title or path.stem.replace("_", " ")
    slug = args.slug or slugify(title)
    quiz = await db.fetchrow("select * from quizzes where slug = $1", slug)
    if quiz is None:
        quiz = await db.fetchrow(
            "insert into quizzes (slug, title, status) values ($1, $2, 'open') returning *", slug, title)
        print(f"created quiz '{slug}'")

    data = path.read_bytes()
    document = await db.fetchrow(
        "insert into documents (quiz_id, filename, storage_key, mime_type, size_bytes) values ($1,$2,$3,$4,$5) returning *",
        quiz["id"], path.name, "pending", MIME_TYPES.get(extension_of(path.name)), len(data))
    key = f"quizzes/reports/{document['id']}/{path.name}"
    await storage().put(key, data, MIME_TYPES.get(extension_of(path.name), "application/octet-stream"))
    await db.execute("update documents set storage_key = $2 where id = $1", document["id"], key)

    await process_document(document["id"])
    result = await db.fetchrow("select * from documents where id = $1", document["id"])
    print(f"\nstatus={result['status']} sections={result['section_count']} passages={result['chunk_count']} "
          f"pictures={result['image_count']} questions={result['question_count']}")
    if result["error"]:
        print("error:", result["error"])
    print("warnings:", result["warnings"])
    print("progress:", result["progress"])
    print("summary:", result["summary"])

    if args.show:
        rows = await db.fetch(
            "select q.*, s.heading from questions q left join doc_sections s on s.id = q.section_id"
            " where q.document_id = $1 order by s.ordinal, q.kind desc, q.created_at limit $2",
            document["id"], args.show)
        for q in rows:
            tag = f"{q['kind']}/{q['source_type']}/{q['skill']}/{q['difficulty']}"
            print(f"\n[{q['heading']}] ({tag})  «{q['title'] or ''}»")
            print("  Q:", q["question_text"])
            if q["kind"] == "mcq":
                for i, option in enumerate(q["options"]):
                    print(f"     {'✔' if i == q['correct_index'] else ' '} {option}")
            else:
                print("     →", "FACT" if q["is_true"] else "FAKE")
            print("  why:", q["explanation"])
            print("  src:", (q["source_quote"] or "")[:150])
    await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
