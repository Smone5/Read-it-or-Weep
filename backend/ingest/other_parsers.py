"""Parsers for the supporting formats: PDF, PowerPoint and plain text / Markdown.

Word is the primary format; these exist so supporting material can be dropped in
alongside it. Each returns the same block list as the Word parser.
"""
from __future__ import annotations

import io
import re
from collections import Counter

from ingest import charts
from ingest.docx_parser import UnreadableDocument, check_ooxml_zip, rows_to_markdown
from ingest.model import Block, ParsedDocument


def parse_text(data: bytes, filename: str = "") -> ParsedDocument:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1", errors="replace")
    parsed = ParsedDocument()
    paragraph: list[str] = []
    table_lines: list[str] = []
    in_code = False
    code_lines: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            parsed.blocks.append(Block(kind="paragraph", text=" ".join(paragraph)))
            paragraph.clear()

    def flush_table() -> None:
        if table_lines:
            if len(table_lines) >= 2:
                parsed.blocks.append(Block(kind="table", text="\n".join(table_lines)))
            else:
                parsed.blocks.append(Block(kind="paragraph", text=table_lines[0]))
            table_lines.clear()

    def flush_code() -> None:
        if code_lines:
            parsed.blocks.append(Block(kind="paragraph", text="\n".join(code_lines)))
            code_lines.clear()

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            flush_paragraph()
            flush_table()
            if in_code:
                flush_code()
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_lines.append(raw.rstrip())
            continue
        if not line:
            flush_paragraph()
            flush_table()
            continue
        if line.startswith("|") and line.count("|") >= 2:
            flush_paragraph()
            table_lines.append(line)
            continue
        flush_table()
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            flush_paragraph()
            level = len(heading.group(1))
            title = heading.group(2).strip(" #")
            if level == 1 and not parsed.title:
                parsed.title = title
            parsed.blocks.append(Block(kind="heading", text=title, level=level))
        elif re.match(r"^([-*•]|\d+[.)])\s+", line):
            flush_paragraph()
            parsed.blocks.append(Block(kind="list_item", text=re.sub(r"^([-*•]|\d+[.)])\s+", "", line)))
        else:
            paragraph.append(line)
    flush_paragraph()
    flush_table()
    flush_code()
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
        layout = page.get_text("dict", sort=True)
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

        page_block_positions: list[tuple[float, int]] = []

        def _emit(block_obj: Block, y_pos: float) -> None:
            parsed.blocks.append(block_obj)
            page_block_positions.append((y_pos, len(parsed.blocks) - 1))

        for block in layout.get("blocks", []):
            bbox = tuple(block.get("bbox") or (0.0, 0.0, 0.0, 0.0))
            if block.get("type") == 1:
                image = block.get("image")
                if image:
                    _emit(Block(kind="image", image=image, mime=f"image/{block.get('ext', 'png')}"), bbox[1])
                continue
            in_table = False
            for t_idx, (tbbox, md, emitted) in enumerate(page_tables):
                if _overlap_ratio(bbox, tbbox) >= 0.55:
                    if not emitted:
                        _emit(Block(kind="table", text=md), tbbox[1])
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
                _emit(Block(kind="heading", text=text, level=toc_levels[norm]), bbox[1])
            elif block_size in larger and _looks_like_heading(text) and not text.endswith("."):
                _emit(Block(kind="heading", text=text, level=larger.index(block_size) + 1), bbox[1])
            elif bold and len(text) <= 80 and _looks_like_heading(text) and not text.endswith("."):
                _emit(Block(kind="heading", text=text, level=len(larger) + 1), bbox[1])
            else:
                _emit(Block(kind="paragraph", text=text), bbox[1])

        # Emit any table that didn't overlap a text block (rare, e.g., image-backed table).
        for tbbox, md, emitted in page_tables:
            if not emitted:
                _emit(Block(kind="table", text=md), tbbox[1])

        # Attach clickable PDF link annotations to the closest block vertically so links above a mid-page heading
        # stay in their own section instead of drifting to the bottom of the page.
        try:
            raw_links = page.get_links() or []
        except Exception:
            raw_links = []
        for link in raw_links:
            uri = link.get("uri")
            if not (isinstance(uri, str) and uri.strip().startswith(("http://", "https://"))):
                continue
            uri = uri.strip()
            if page_block_positions:
                rect = link.get("from")
                ly = float(rect.y0) if rect is not None and hasattr(rect, "y0") else page_h
                _, target_idx = min(page_block_positions, key=lambda pair: abs(pair[0] - ly))
                parsed.blocks[target_idx].links.append(uri)
            elif parsed.blocks:
                parsed.blocks[-1].links.append(uri)
    document.close()
    return parsed


def _pptx_chart_markdown(chart) -> tuple[str | None, str]:
    """Extract a native PowerPoint chart's title and series/category values into markdown."""
    title = None
    try:
        if chart.has_title and chart.chart_title.has_text_frame:
            title = chart.chart_title.text_frame.text.strip() or None
    except Exception:
        pass
    try:
        plots = list(chart.plots)
        if not plots:
            return title, ""
        plot = plots[0]
        categories = [str(c.label).strip() for c in (plot.categories or [])]
        series_list = list(plot.series or [])
        if not series_list:
            return title, ""
        header = ["Category", *[str(s.name or f"Series {i + 1}").strip() for i, s in enumerate(series_list)]]
        rows = [header]
        n_rows = max(len(categories), max((len(s.values or []) for s in series_list), default=0))
        for r_idx in range(n_rows):
            cat = categories[r_idx] if r_idx < len(categories) else f"Item {r_idx + 1}"
            vals = [
                ("" if r_idx >= len(s.values or []) or s.values[r_idx] is None else str(s.values[r_idx]))
                for s in series_list
            ]
            rows.append([cat, *vals])
        return title, rows_to_markdown(rows)
    except Exception:
        return title, ""


def parse_pptx(data: bytes, filename: str = "") -> ParsedDocument:
    from pptx import Presentation

    check_ooxml_zip(data, "PowerPoint (.pptx) presentation")
    try:
        deck = Presentation(io.BytesIO(data))
    except Exception as exc:
        raise UnreadableDocument("This PowerPoint file could not be opened (use .pptx).") from exc
    parsed = ParsedDocument(title=(deck.core_properties.title or "").strip() or None)

    def walk(shapes, title_shape) -> int:
        content_blocks = 0
        for shape in shapes:
            if shape is title_shape:
                continue
            if shape.shape_type == 6:  # group
                content_blocks += walk(shape.shapes, title_shape)
                continue
            if getattr(shape, "has_table", False) and shape.has_table:
                rows = [[cell.text.strip() for cell in row.cells] for row in shape.table.rows]
                rows = [r for r in rows if any(r)]
                if len(rows) >= 2:
                    parsed.blocks.append(Block(kind="table", text=rows_to_markdown(rows)))
                    content_blocks += 1
                continue
            if getattr(shape, "has_chart", False) and shape.has_chart:
                chart_data = None
                png = None
                try:
                    chart_data = charts.parse_chart_xml(shape.chart.part.blob)
                    if chart_data is not None:
                        png = chart_data.render_png()
                except Exception:
                    chart_data = None
                    png = None
                if chart_data is not None:
                    parsed.blocks.append(Block(
                        kind="chart",
                        text=chart_data.as_text(),
                        image=png,
                        mime="image/png" if png else None,
                        caption=chart_data.title,
                        origin="chart_render" if png else "embedded",
                    ))
                    content_blocks += 1
                else:
                    c_title, md = _pptx_chart_markdown(shape.chart)
                    if md.strip():
                        parsed.blocks.append(Block(kind="chart", text=md, caption=c_title))
                        content_blocks += 1
                continue
            if shape.shape_type == 13 and hasattr(shape, "image"):  # picture
                try:
                    parsed.blocks.append(Block(kind="image", image=shape.image.blob, mime=shape.image.content_type))
                    content_blocks += 1
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
                        content_blocks += 1
                    elif run_links and parsed.blocks:
                        parsed.blocks[-1].links.extend(run_links)
        return content_blocks

    slide_entries: list[tuple[int, int]] = []  # (heading_block_idx, content_blocks)
    for number, slide in enumerate(deck.slides, start=1):
        title_shape = slide.shapes.title
        title = (title_shape.text.strip() if title_shape is not None and title_shape.has_text_frame else "") or f"Slide {number}"
        heading_idx = len(parsed.blocks)
        parsed.blocks.append(Block(kind="heading", text=title, level=1))
        body_count = walk(slide.shapes, title_shape)
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                parsed.blocks.append(Block(kind="paragraph", text=f"Speaker notes: {notes}"))
                body_count += 1
        slide_entries.append((heading_idx, body_count))

    # If the deck uses section-divider slides (title-only slides with 0 body blocks) alongside content slides,
    # demote content slides to level=2 so sections group naturally instead of merging arbitrarily past 14 slides.
    dividers = [idx for idx, body in slide_entries if body == 0]
    if len(dividers) >= 2 and len(slide_entries) > len(dividers) + 2:
        for idx, body in slide_entries:
            if body > 0:
                parsed.blocks[idx].level = 2

    if not any(b.kind in ("paragraph", "list_item", "table", "chart") for b in parsed.blocks):
        raise UnreadableDocument("No readable text was found in this presentation.")
    return parsed
