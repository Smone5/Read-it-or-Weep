"""Background job that turns an uploaded document into a ready question bank.

Stages (visible to the admin as progress): reading → storing → indexing → writing → ready.
A failure at any point marks the document as failed with a message the admin can act on;
"Process again" simply re-runs this job.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

import numpy as np

import db
import llm
from ingest import UnreadableDocument, generate, parse_document
from ingest.model import StructuredDocument
from services import settings_store, video
from storage import storage

log = logging.getLogger("pipeline")

_running: set[asyncio.Task] = set()


def start_processing(document_id: str) -> None:
    """Run the job in the background and keep a reference so it is not garbage-collected."""
    task = asyncio.create_task(process_document(document_id))
    _running.add(task)
    task.add_done_callback(_running.discard)


async def _set(document_id: str, **fields) -> None:
    columns = ", ".join(f"{name} = ${i}" for i, name in enumerate(fields, start=2))
    await db.execute(f"update documents set {columns}, heartbeat_at = now() where id = $1", document_id, *fields.values())


async def clear_derived(document_id: str) -> None:
    """Remove everything extracted from a document (it is about to be re-read or deleted)."""
    await db.execute("delete from questions where document_id = $1 and origin = 'generated' and edited_at is null",
                     document_id)
    # Questions that are kept (hand-written or edited) lose their links into the old text. One that was about
    # a picture cannot be asked without it, so it goes back to the review queue instead of into a game.
    await db.execute(
        "update questions set status = 'pending', quality = coalesce(quality, '{}'::jsonb)"
        " || jsonb_build_object('pulled', 'Its picture was removed when the report was re-read. Choose a picture again.')"
        " where document_id = $1 and image_id is not null", document_id)
    await db.execute("update questions set chunk_id = null, image_id = null, section_id = null where document_id = $1",
                     document_id)
    await db.execute("delete from doc_images where document_id = $1", document_id)
    await db.execute("delete from doc_chunks where document_id = $1", document_id)
    await db.execute("delete from doc_sections where document_id = $1", document_id)
    await storage().delete_prefix(f"quizzes/images/{document_id}")


async def _store_structure(document: dict, structured: StructuredDocument) -> tuple[list[str], list[str], list[str]]:
    document_id, quiz_id = document["id"], document["quiz_id"]
    section_ids: list[str] = []
    for section in structured.sections:
        section_ids.append(await db.fetchval(
            "insert into doc_sections (document_id, quiz_id, ordinal, level, heading, word_count)"
            " values ($1, $2, $3, $4, $5, $6) returning id",
            document_id, quiz_id, section.ordinal, section.level, section.heading[:300], section.word_count))
    chunk_ids: list[str] = []
    for chunk in structured.chunks:
        chunk_ids.append(await db.fetchval(
            "insert into doc_chunks (document_id, quiz_id, section_id, ordinal, kind, heading_path, chunk_text, token_count)"
            " values ($1, $2, $3, $4, $5, $6, $7, $8) returning id",
            document_id, quiz_id, section_ids[chunk.section], chunk.ordinal, chunk.kind,
            chunk.heading_path[:500], chunk.text, chunk.token_count))
    image_ids: list[str] = []
    for image in structured.images:
        extension = "jpg" if image.mime == "image/jpeg" else "png"
        key = f"quizzes/images/{document_id}/{image.ordinal:03d}.{extension}"
        await storage().put(key, image.data, image.mime)
        image_ids.append(await db.fetchval(
            "insert into doc_images (document_id, quiz_id, section_id, ordinal, storage_key, mime_type, width, height,"
            " sha256, origin, caption, context_text) values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) returning id",
            document_id, quiz_id, section_ids[image.section], image.ordinal, key, image.mime, image.width,
            image.height, image.sha256, image.origin, image.caption, image.context))
    return section_ids, chunk_ids, image_ids


async def _existing_question_vectors(quiz_id: str) -> np.ndarray | None:
    rows = await db.fetch("select embedding from questions where quiz_id = $1 and embedding is not null", quiz_id)
    if not rows:
        return None
    return np.asarray([r["embedding"] for r in rows], dtype=np.float32)


async def _insert_questions(document: dict, report: generate.GenerationReport, section_ids: list[str],
                            chunk_ids: list[str], image_ids: list[str], status: str) -> int:
    rows = []
    for q in report.questions:
        rows.append((
            document["quiz_id"], document["id"], section_ids[q.section_index],
            chunk_ids[q.chunk_index] if q.chunk_index is not None else None,
            image_ids[q.image_index] if q.image_index is not None else None,
            q.kind, q.source_type, q.skill, q.title, q.question_text, q.options, q.correct_index, q.is_true,
            q.explanation, q.source_quote, q.difficulty, status, q.quality,
            q.embedding.tolist() if q.embedding is not None else None,
        ))
    await db.executemany(
        "insert into questions (quiz_id, document_id, section_id, chunk_id, image_id, kind, source_type, skill, title,"
        " question_text, options, correct_index, is_true, explanation, source_quote, difficulty, status, quality,"
        " embedding) values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)", rows)
    return len(rows)


async def _attach_section_videos(structured: StructuredDocument, section_ids: list[str]) -> int:
    """Attach YouTube links found inside uploaded document sections to up to 2 MCQ questions per clip."""
    attached = 0
    for section, section_id in zip(structured.sections, section_ids):
        if not section.video_urls:
            continue
        mcqs = await db.fetch(
            "select id from questions where section_id = $1 and kind = 'mcq' and video_url is null"
            " order by pinned desc, created_at asc",
            section_id,
        )
        if not mcqs:
            continue
        idx = 0
        for raw_url in section.video_urls[:2]:
            if idx >= len(mcqs):
                break
            try:
                info = await video.inspect_youtube(raw_url, timeout=6.0)
                stored_url = info["normalizedUrl"]
                title = info["title"] or section.heading[:100]
            except Exception as exc:
                log.warning("skipping unverified YouTube URL %s: %s", raw_url, exc)
                continue
            targets = mcqs[idx:idx + 2]
            idx += len(targets)
            for row in targets:
                await db.execute(
                    "update questions set video_url = $2, video_title = $3 where id = $1",
                    row["id"], stored_url, title[:120],
                )
                attached += 1
    return attached


async def process_document(document_id: str) -> None:
    document = await db.fetchrow("select * from documents where id = $1", document_id)
    if document is None:
        return
    quiz = await db.fetchrow("select * from quizzes where id = $1", document["quiz_id"])
    rules = await settings_store.quiz_rules(quiz)
    auto_approve = bool(rules["auto_approve"])   # the quiz's setting, else the company's
    purge_source = bool(rules.get("purge_source_after_process"))

    async def progress(label: str, done: int, total: int) -> None:
        await _set(document_id, progress={"label": label, "done": done, "total": total})

    try:
        await _set(document_id, status="processing", stage="reading", error=None,
                   progress={"label": "Reading the document", "done": 0, "total": 1})
        data = await storage().get(document["storage_key"])
        structured = await asyncio.to_thread(parse_document, data, document["filename"])

        untitled = [i for i, s in enumerate(structured.sections) if re.fullmatch(r"Part \d+ of \d+", s.heading)]
        if untitled:
            samples = [" ".join(c.text for c in structured.chunks if c.section == i) for i in untitled]
            try:
                for index, name in zip(untitled, await generate.name_sections(samples)):
                    if name:
                        structured.sections[index].heading = name
                        for chunk in structured.chunks:
                            if chunk.section == index:
                                chunk.heading_path = name
            except Exception as exc:
                log.warning("could not name sections: %s", exc)

        await _set(document_id, stage="storing", progress={"label": "Saving sections and pictures", "done": 0, "total": 1})
        await clear_derived(document_id)
        section_ids, chunk_ids, image_ids = await _store_structure(document, structured)

        await _set(document_id, stage="indexing", progress={"label": "Indexing the text", "done": 0, "total": 1})
        embeddings = await llm.embed([c.text for c in structured.chunks])
        await db.executemany("update doc_chunks set embedding = $2 where id = $1",
                             [(chunk_id, vector.tolist()) for chunk_id, vector in zip(chunk_ids, embeddings)])

        await _set(document_id, stage="writing")
        report = await generate.generate_bank(structured, embeddings, progress=progress,
                                              existing_embeddings=await _existing_question_vectors(document["quiz_id"]))
        for ordinal, (kind, description, usable) in report.image_notes.items():
            await db.execute("update doc_images set kind = $2, description = $3, usable = $4 where id = $1",
                             image_ids[ordinal], kind, description, usable)
        inserted = await _insert_questions(document, report, section_ids, chunk_ids, image_ids,
                                           "approved" if auto_approve else "pending")
        if inserted and any(s.video_urls for s in structured.sections):
            videos_attached = await _attach_section_videos(structured, section_ids)
            if videos_attached:
                log.info("auto-attached %d YouTube clip(s) extracted from %s", videos_attached, document["filename"])

        summary_text = None
        try:
            summary = await generate.summarise(structured)
            summary_text = summary.summary.strip()
            if summary.themes:
                summary_text += "\nKey themes: " + "; ".join(t.strip() for t in summary.themes[:6])
        except Exception as exc:
            log.warning("could not summarise: %s", exc)

        warnings = list(structured.warnings)
        if not inserted:
            warnings.append("No questions passed the quality checks. Add questions by hand or try a fuller document.")
        storage_key = document["storage_key"]
        if purge_source and storage_key:
            try:
                await storage().delete_prefix(storage_key)
                storage_key = ""
                warnings.append("Source file was purged after processing (confidential retention mode).")
            except Exception as exc:
                log.warning("could not purge source file %s: %s", storage_key, exc)
        await _set(
            document_id, status="ready", stage=None, title=structured.title, summary=summary_text,
            storage_key=storage_key,
            word_count=structured.word_count, section_count=len(structured.sections),
            image_count=len(structured.images), chunk_count=len(structured.chunks), question_count=inserted,
            warnings=warnings, processed_at=datetime.now(timezone.utc),
            progress={"label": "Ready", "done": 1, "total": 1, "drafted": report.drafted, "kept": inserted,
                      "rejected": report.rejected})
        log.info("document %s ready: %s questions kept of %s drafted", document_id, inserted, report.drafted)
    except UnreadableDocument as exc:
        await _set(document_id, status="failed", stage=None, error=str(exc))
    except llm.AIUnavailable as exc:
        await _set(document_id, status="failed", stage=None,
                   error=f"The AI service could not be reached, so no questions were written. ({exc})")
    except Exception as exc:  # noqa: BLE001 – report anything unexpected to the admin instead of hanging
        log.exception("processing failed for %s", document_id)
        await _set(document_id, status="failed", stage=None,
                   error=f"Something went wrong while processing this document ({type(exc).__name__}: {exc}).")


def start_more_questions(document_id: str) -> None:
    task = asyncio.create_task(more_questions(document_id))
    _running.add(task)
    task.add_done_callback(_running.discard)


async def more_questions(document_id: str) -> None:
    """Second helping: write additional questions about facts the bank does not cover yet."""
    from ingest.model import Chunk, Section

    document = await db.fetchrow("select * from documents where id = $1", document_id)
    if document is None or document["status"] != "ready":
        return
    quiz = await db.fetchrow("select * from quizzes where id = $1", document["quiz_id"])
    auto_approve = bool((await settings_store.quiz_rules(quiz))["auto_approve"])
    sections = await db.fetch("select * from doc_sections where document_id = $1 order by ordinal", document_id)
    chunks = await db.fetch("select * from doc_chunks where document_id = $1 order by ordinal", document_id)
    if not chunks:
        return
    section_index = {s["id"]: i for i, s in enumerate(sections)}
    structured = StructuredDocument(
        title=document["title"] or document["filename"],
        sections=[Section(ordinal=i, level=s["level"], heading=s["heading"], word_count=s["word_count"])
                  for i, s in enumerate(sections)],
        chunks=[Chunk(ordinal=i, section=section_index.get(c["section_id"], 0), kind=c["kind"],
                      heading_path=c["heading_path"] or "", text=c["chunk_text"], token_count=c["token_count"] or 0)
                for i, c in enumerate(chunks)],
        images=[], warnings=[], word_count=document["word_count"] or 0)
    existing = await db.fetch("select chunk_id, question_text from questions where document_id = $1", document_id)
    chunk_position = {c["id"]: i for i, c in enumerate(chunks)}
    avoid: dict[int, list[str]] = {}
    for row in existing:
        if row["chunk_id"] in chunk_position:
            avoid.setdefault(chunk_position[row["chunk_id"]], []).append(row["question_text"])
    try:
        await _set(document_id, stage="writing", progress={"label": "Writing more questions", "done": 0, "total": len(chunks)})
        if any(c["embedding"] is None for c in chunks):
            embeddings = await llm.embed([c["chunk_text"] for c in chunks])
        else:
            embeddings = np.asarray([c["embedding"] for c in chunks], dtype=np.float32)

        async def progress(label: str, done: int, total: int) -> None:
            await _set(document_id, progress={"label": "Writing more questions", "done": done, "total": total})

        report = await generate.generate_bank(
            structured, embeddings, progress=progress, avoid_by_chunk=avoid, include_images=False,
            existing_embeddings=await _existing_question_vectors(document["quiz_id"]))
        inserted = await _insert_questions(document, report, [s["id"] for s in sections], [c["id"] for c in chunks], [],
                                           "approved" if auto_approve else "pending")
        total = await db.fetchval("select count(*) from questions where document_id = $1", document_id)
        await _set(document_id, stage=None, question_count=total,
                   progress={"label": "Ready", "done": 1, "total": 1, "added": inserted, "rejected": report.rejected})
    except Exception as exc:  # noqa: BLE001
        log.exception("writing more questions failed for %s", document_id)
        await _set(document_id, stage=None, progress={"label": f"Could not write more questions ({type(exc).__name__})",
                                                       "done": 1, "total": 1})
