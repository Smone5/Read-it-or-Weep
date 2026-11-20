"""Parsers for the supporting formats: PDF, PowerPoint and plain text / Markdown.

Word is the primary format; these exist so supporting material can be dropped in
alongside it. Each returns the same block list as the Word parser.
"""
from __future__ import annotations

import io
import re
from collections import Counter

from ingest.docx_parser import UnreadableDocument, rows_to_markdown
from ingest.model import Block, ParsedDocument


def parse_text(data: bytes, filename: str = "") -> ParsedDocument:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1", errors="replace")
    parsed = ParsedDocument()
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            parsed.blocks.append(Block(kind="paragraph", text=" ".join(paragraph)))
            paragraph.clear()

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            flush()
            level = len(heading.group(1))
            title = heading.group(2).strip(" #")
            if level == 1 and not parsed.title:
                parsed.title = title
            parsed.blocks.append(Block(kind="heading", text=title, level=level))
        elif re.match(r"^([-*•]|\d+[.)])\s+", line):
            flush()
            parsed.blocks.append(Block(kind="list_item", text=re.sub(r"^([-*•]|\d+[.)])\s+", "", line)))
        else:
            paragraph.append(line)
    flush()
    if not parsed.blocks:
        raise UnreadableDocument("No readable text was found in this file.")
    return parsed


def parse_pdf(data: bytes, filename: str = "") -> ParsedDocument:
    import pymupdf

    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise UnreadableDocument("This PDF could not be opened.") from exc
    if document.needs_pass:
        raise UnreadableDocument("This PDF is password-protected.")
    parsed = ParsedDocument(title=(document.metadata or {}).get("title") or None)

    # Body text size = the most common font size; clearly larger lines are headings.
    sizes: Counter[float] = Counter()
    pages = []
    for page in document:
        layout = page.get_text("dict")
        pages.append((page, layout))
        for block in layout.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span.get("text", "").strip():
                        sizes[round(span["size"], 1)] += len(span["text"])
    if not sizes:
        raise UnreadableDocument(
            "This PDF has no selectable text (it may be a scan). Export the report as Word or a text PDF.")
    body_size = sizes.most_common(1)[0][0]
    larger = sorted({s for s in sizes if s >= body_size * 1.12}, reverse=True)[:3]

    for page, layout in pages:
        for block in layout.get("blocks", []):
            if block.get("type") == 1:
                image = block.get("image")
                if image:
                    parsed.blocks.append(Block(kind="image", image=image, mime=f"image/{block.get('ext', 'png')}"))
                continue
            lines = []
            block_size = 0.0
            bold = True
            for line in block.get("lines", []):
                spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
                if not spans:
                    continue
                lines.append("".join(s["text"] for s in spans).strip())
                block_size = max(block_size, max(round(s["size"], 1) for s in spans))
                bold = bold and all("bold" in s.get("font", "").lower() or s.get("flags", 0) & 16 for s in spans)
            text = re.sub(r"\s+", " ", " ".join(lines)).strip()
            if not text or re.fullmatch(r"(page\s*)?\d+(\s*(of|/)\s*\d+)?", text, flags=re.I):
                continue
            if block_size in larger and len(text) <= 120:
                parsed.blocks.append(Block(kind="heading", text=text, level=larger.index(block_size) + 1))
            elif bold and len(text) <= 80 and not text.endswith("."):
                parsed.blocks.append(Block(kind="heading", text=text, level=len(larger) + 1))
            else:
                parsed.blocks.append(Block(kind="paragraph", text=text))
    document.close()
    return parsed


def parse_pptx(data: bytes, filename: str = "") -> ParsedDocument:
    from pptx import Presentation
    from pptx.util import Emu

    try:
        deck = Presentation(io.BytesIO(data))
    except Exception as exc:
        raise UnreadableDocument("This PowerPoint file could not be opened (use .pptx).") from exc
    parsed = ParsedDocument(title=(deck.core_properties.title or "").strip() or None)

    def walk(shapes, title_shape) -> None:
        for shape in shapes:
            if shape is title_shape:
                continue
            if shape.shape_type == 6:  # group
                walk(shape.shapes, title_shape)
                continue
            if getattr(shape, "has_table", False) and shape.has_table:
                rows = [[cell.text.strip() for cell in row.cells] for row in shape.table.rows]
                rows = [r for r in rows if any(r)]
                if len(rows) >= 2:
                    parsed.blocks.append(Block(kind="table", text=rows_to_markdown(rows)))
                continue
            if shape.shape_type == 13 and hasattr(shape, "image"):  # picture
                try:
                    parsed.blocks.append(Block(kind="image", image=shape.image.blob, mime=shape.image.content_type))
                except Exception:
                    pass
                continue
            if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    text = "".join(run.text for run in paragraph.runs).strip()
                    if text:
                        parsed.blocks.append(Block(kind="list_item" if paragraph.level else "paragraph", text=text))

    for number, slide in enumerate(deck.slides, start=1):
        title_shape = slide.shapes.title
        title = (title_shape.text.strip() if title_shape is not None and title_shape.has_text_frame else "") or f"Slide {number}"
        parsed.blocks.append(Block(kind="heading", text=title, level=1))
        walk(slide.shapes, title_shape)
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                parsed.blocks.append(Block(kind="paragraph", text=f"Speaker notes: {notes}"))
    if not any(b.kind in ("paragraph", "list_item", "table") for b in parsed.blocks):
        raise UnreadableDocument("No readable text was found in this presentation.")
    return parsed
