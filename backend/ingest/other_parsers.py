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


_JUNK_TITLE = re.compile(
    r"^(printmgr(\s+file)?|untitled.*|document\s*\d*|slide\s*\d*|microsoft\s+word\b.*|powerpoint\b.*|presentation\s*\d*|.*\.(docx?|pdf|pptx?))$",
    re.I,
)


def _clean_pdf_title(raw: str | None) -> str | None:
    title = re.sub(r"\s+", " ", (raw or "")).strip()
    if not title or len(title) < 3 or _JUNK_TITLE.match(title):
        return None
    return title


def _looks_like_heading(text: str) -> bool:
    if not text or len(text) < 2 or len(text) > 120:
        return False
    if text[0] in "($%•-*·" or text.endswith((",", ";")):
        return False
    if "http://" in text.lower() or "https://" in text.lower() or "doi.org" in text.lower():
        return False
    if text.count("(") != text.count(")") or text.count("[") != text.count("]"):
        return False
    if re.fullmatch(r"[\d\s.,:%$€£/–-]+", text):
        return False
    return True


def _overlap_ratio(inner: tuple[float, float, float, float], outer: tuple[float, float, float, float]) -> float:
    ix0, iy0, ix1, iy1 = inner
    ox0, oy0, ox1, oy1 = outer
    dx = max(0.0, min(ix1, ox1) - max(ix0, ox0))
    dy = max(0.0, min(iy1, oy1) - max(iy0, oy0))
    area = max(1.0, (ix1 - ix0) * (iy1 - iy0))
    return (dx * dy) / area


def _norm_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def parse_pdf(data: bytes, filename: str = "") -> ParsedDocument:
    import pymupdf

    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise UnreadableDocument("This PDF could not be opened.") from exc
    if document.needs_pass:
        raise UnreadableDocument("This PDF is password-protected.")
    parsed = ParsedDocument(title=_clean_pdf_title((document.metadata or {}).get("title")))

    # Native PDF bookmarks (when present) give authoritative chapter titles & levels.
    toc_levels: dict[str, int] = {}
    try:
        for lvl, title, _ in document.get_toc() or []:
            key = _norm_key(title)
            if key and 1 <= lvl <= 3 and key not in toc_levels:
                toc_levels[key] = lvl
    except Exception:
        pass

    # Body text size = the most common font size; clearly larger lines are headings.
    sizes: Counter[float] = Counter()
    body_page_sizes: Counter[float] = Counter()
    margin_counts: Counter[str] = Counter()
    pages = []
    page_count = len(document)
    for page_idx, page in enumerate(document):
        layout = page.get_text("dict")
        rect = page.rect
        pages.append((page_idx, page, layout, rect.height))
        seen_margin: set[str] = set()
        for block in layout.get("blocks", []):
            if block.get("type") != 0:
                continue
            bbox = block.get("bbox") or (0, 0, 0, 0)
            b_lines = []
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    st = span.get("text", "").strip()
                    if st:
                        sz = round(span["size"], 1)
                        sizes[sz] += len(st)
                        if page_count < 4 or page_idx > 0:
                            body_page_sizes[sz] += len(st)
                        b_lines.append(st)
            if page_count >= 4 and b_lines and (bbox[1] < rect.height * 0.09 or bbox[3] > rect.height * 0.91):
                key = _norm_key(" ".join(b_lines))
                if key and len(key) <= 100 and key not in seen_margin:
                    seen_margin.add(key)
                    margin_counts[key] += 1
    if not sizes:
        raise UnreadableDocument(
            "This PDF has no selectable text (it may be a scan). Export the report as Word or a text PDF.")

    running_headers = {k for k, c in margin_counts.items() if c >= max(3, int(page_count * 0.3))}
    body_size = sizes.most_common(1)[0][0]
    # Exclude cover-page-only outlier sizes when determining heading tiers on multi-page PDFs.
    tier_source = body_page_sizes if body_page_sizes else sizes
    larger = sorted({s for s in tier_source if s >= body_size * 1.12}, reverse=True)[:3]

    for page_idx, page, layout, page_h in pages:
        # Extract structured tables via PyMuPDF and avoid duplicating their text as mangled paragraphs.
        page_tables: list[tuple[tuple[float, float, float, float], str, bool]] = []
        try:
            found = page.find_tables()
            for tbl in (found.tables if found else []):
                raw_rows = tbl.extract() or []
                clean_rows = [[re.sub(r"\s+", " ", (cell or "")).strip() for cell in row] for row in raw_rows]
                clean_rows = [r for r in clean_rows if any(r)]
                if len(clean_rows) >= 2 and len(clean_rows[0]) >= 2:
                    md = rows_to_markdown(clean_rows)
                    if md.strip():
                        page_tables.append((tuple(tbl.bbox), md, False))
        except Exception:
            page_tables = []

        for block in layout.get("blocks", []):
            if block.get("type") == 1:
                image = block.get("image")
                if image:
                    parsed.blocks.append(Block(kind="image", image=image, mime=f"image/{block.get('ext', 'png')}"))
                continue
            bbox = tuple(block.get("bbox") or (0.0, 0.0, 0.0, 0.0))
            in_table = False
            for t_idx, (tbbox, md, emitted) in enumerate(page_tables):
                if _overlap_ratio(bbox, tbbox) >= 0.55:
                    if not emitted:
                        parsed.blocks.append(Block(kind="table", text=md))
                        page_tables[t_idx] = (tbbox, md, True)
                    in_table = True
                    break
            if in_table:
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
            norm = _norm_key(text)
            if norm in running_headers and (bbox[1] < page_h * 0.11 or bbox[3] > page_h * 0.89):
                continue

            if norm in toc_levels and _looks_like_heading(text):
                parsed.blocks.append(Block(kind="heading", text=text, level=toc_levels[norm]))
            elif block_size in larger and _looks_like_heading(text) and not text.endswith("."):
                parsed.blocks.append(Block(kind="heading", text=text, level=larger.index(block_size) + 1))
            elif bold and len(text) <= 80 and _looks_like_heading(text) and not text.endswith("."):
                parsed.blocks.append(Block(kind="heading", text=text, level=len(larger) + 1))
            else:
                parsed.blocks.append(Block(kind="paragraph", text=text))

        # Emit any table that didn't overlap a text block (rare, e.g., image-backed table).
        for _, md, emitted in page_tables:
            if not emitted:
                parsed.blocks.append(Block(kind="table", text=md))

        # Capture clickable PDF link annotations (e.g. hyperlinked YouTube URLs on this page).
        try:
            page_uris = [
                str(link["uri"]).strip()
                for link in (page.get_links() or [])
                if isinstance(link.get("uri"), str) and str(link["uri"]).strip().startswith(("http://", "https://"))
            ]
        except Exception:
            page_uris = []
        if page_uris and parsed.blocks:
            parsed.blocks[-1].links.extend(page_uris)
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
                shape_link: list[str] = []
                try:
                    addr = shape.click_action.hyperlink.address
                    if isinstance(addr, str) and addr.startswith(("http://", "https://")):
                        shape_link.append(addr)
                except Exception:
                    pass
                for p_idx, paragraph in enumerate(shape.text_frame.paragraphs):
                    text = "".join(run.text for run in paragraph.runs).strip()
                    run_links = list(shape_link) if p_idx == 0 else []
                    for run in paragraph.runs:
                        try:
                            addr = run.hyperlink.address
                            if isinstance(addr, str) and addr.startswith(("http://", "https://")):
                                run_links.append(addr)
                        except Exception:
                            pass
                    if text:
                        parsed.blocks.append(Block(
                            kind="list_item" if paragraph.level else "paragraph",
                            text=text,
                            links=run_links,
                        ))
                    elif run_links and parsed.blocks:
                        parsed.blocks[-1].links.extend(run_links)

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
