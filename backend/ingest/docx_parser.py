"""Word (.docx) parser.

Reads the document body in reading order and produces format-neutral blocks:
headings (from real heading styles / outline levels, with a fallback for documents
that only use bold lines), paragraphs, list items, tables (as markdown), pictures,
native Word charts (data + a rendered image) and the text inside text boxes.

Tracked-change insertions are included; deletions are ignored.
"""
from __future__ import annotations

import io
import re

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from lxml import etree

from ingest.charts import parse_chart_xml
from ingest.model import Block, ParsedDocument

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "v": "urn:schemas-microsoft-com:vml",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
}
W = "{%s}" % NS["w"]
R = "{%s}" % NS["r"]
MC = "{%s}" % NS["mc"]

# Sub-trees whose text must not be read as part of the surrounding paragraph.
_SKIP_SUBTREES = {W + "txbxContent", MC + "Fallback", W + "del", W + "moveFrom", W + "pPr", W + "rPr"}

CAPTION_RE = re.compile(r"^(figure|fig\.?|chart|exhibit|graph|diagram|image|illustration|table)\s+[A-Za-z]?\d+", re.I)
NUMBERED_HEADING_RE = re.compile(r"^(\d+(?:\.\d+){0,3})[.)]?\s+\S")


class UnreadableDocument(ValueError):
    pass


def _text_of(element) -> str:
    """Visible text of a paragraph-like element, skipping text boxes, fallbacks and deletions."""
    parts: list[str] = []

    def walk(node) -> None:
        for child in node:
            tag = child.tag
            if tag in _SKIP_SUBTREES:
                continue
            if tag == W + "t":
                parts.append(child.text or "")
            elif tag == W + "tab":
                parts.append(" ")
            elif tag in (W + "br", W + "cr"):
                parts.append("\n")
            elif tag == W + "noBreakHyphen":
                parts.append("-")
            else:
                walk(child)

    walk(element)
    return re.sub(r"[ \t ]+", " ", "".join(parts)).strip()


def _has_fallback_ancestor(node) -> bool:
    parent = node.getparent()
    while parent is not None:
        if parent.tag == MC + "Fallback":
            return True
        parent = parent.getparent()
    return False


class _Styles:
    """Resolves a paragraph style id to: heading level, caption, table-of-contents, list, title."""

    def __init__(self, document) -> None:
        self.level: dict[str, int] = {}
        self.caption: set[str] = set()
        self.toc: set[str] = set()
        self.title: set[str] = set()
        self.subtitle: set[str] = set()
        self.list: set[str] = set()
        self.bold: set[str] = set()
        elements = {}
        for style in document.styles.element.findall(W + "style"):
            style_id = style.get(W + "styleId")
            if style_id:
                elements[style_id] = style

        def resolve(style_id: str, seen: frozenset = frozenset()) -> int:
            if style_id in self.level:
                return self.level[style_id]
            style = elements.get(style_id)
            if style is None or style_id in seen:
                return 0
            name_el = style.find(W + "name")
            name = (name_el.get(W + "val") if name_el is not None else "") or ""
            lowered = name.lower().strip()
            level = 0
            match = re.match(r"heading\s*(\d)", lowered)
            if match:
                level = int(match.group(1))
            outline = style.find(f"{W}pPr/{W}outlineLvl")
            if not level and outline is not None:
                value = int(outline.get(W + "val", "9"))
                level = value + 1 if 0 <= value <= 8 else 0
            if not level:
                based_on = style.find(W + "basedOn")
                if based_on is not None:
                    level = resolve(based_on.get(W + "val", ""), seen | {style_id})
            if lowered == "title":
                self.title.add(style_id)
                level = 0
            elif lowered == "subtitle":
                self.subtitle.add(style_id)
                level = 0
            elif lowered.startswith("toc") or lowered.startswith("table of"):
                self.toc.add(style_id)
                level = 0
            elif lowered == "caption":
                self.caption.add(style_id)
                level = 0
            elif lowered.startswith("list"):
                self.list.add(style_id)
            bold = style.find(f"{W}rPr/{W}b")
            if bold is not None and bold.get(W + "val", "true") not in ("0", "false"):
                self.bold.add(style_id)
            self.level[style_id] = level
            return level

        for style_id in elements:
            resolve(style_id)


def _iter_body(container):
    """Yield paragraphs and tables in order, looking inside content controls."""
    for child in container:
        if child.tag in (W + "p", W + "tbl"):
            yield child
        elif child.tag == W + "sdt":
            content = child.find(W + "sdtContent")
            if content is not None:
                yield from _iter_body(content)
        elif child.tag in (W + "ins", W + "moveTo", W + "customXml", W + "smartTag"):
            yield from _iter_body(child)


def _is_all_bold(paragraph, styles: _Styles, style_id: str) -> bool:
    runs = [r for r in paragraph.iter(W + "r") if _text_of(r)]
    if not runs:
        return False
    for run in runs:
        bold = run.find(f"{W}rPr/{W}b")
        if bold is not None:
            if bold.get(W + "val", "true") in ("0", "false"):
                return False
        elif style_id not in styles.bold:
            return False
    return True


def _pseudo_heading_level(text: str, all_bold: bool) -> int:
    """Heading guess for documents that do not use heading styles."""
    if not (3 <= len(text) <= 90) or "\n" in text or text.endswith((".", ",", ";")):
        return 0
    numbered = NUMBERED_HEADING_RE.match(text)
    if numbered and (all_bold or len(text) <= 60):
        return min(numbered.group(1).count(".") + 1, 4)
    if all_bold and len(text.split()) <= 12:
        return 1
    return 0


def _cell_text(cell) -> str:
    return " / ".join(t for t in (_text_of(p) for p in cell.iter(W + "p")) if t)


def _table_rows(table) -> list[list[str]]:
    rows: list[list[str]] = []
    for tr in table.iterchildren(W + "tr"):
        cells: list[str] = []
        for tc in tr.iterchildren(W + "tc"):
            properties = tc.find(W + "tcPr")
            span = 1
            continued = False
            if properties is not None:
                grid_span = properties.find(W + "gridSpan")
                if grid_span is not None:
                    span = max(1, int(grid_span.get(W + "val", "1") or 1))
                v_merge = properties.find(W + "vMerge")
                continued = v_merge is not None and v_merge.get(W + "val", "continue") != "restart"
            cells.append("" if continued else _cell_text(tc))
            cells.extend([""] * (span - 1))
        if any(cells):
            rows.append(cells)
    width = max((len(r) for r in rows), default=0)
    return [r + [""] * (width - len(r)) for r in rows]


def rows_to_markdown(rows: list[list[str]]) -> str:
    def clean(value: str) -> str:
        return value.replace("|", "/").replace("\n", " ").strip()

    header, *body = rows
    lines = ["| " + " | ".join(clean(c) for c in header) + " |",
             "| " + " | ".join("---" for _ in header) + " |"]
    lines += ["| " + " | ".join(clean(c) for c in row) + " |" for row in body]
    return "\n".join(lines)


def parse_docx(data: bytes, filename: str = "") -> ParsedDocument:
    try:
        document = Document(io.BytesIO(data))
    except (PackageNotFoundError, KeyError, ValueError, etree.XMLSyntaxError) as exc:
        raise UnreadableDocument(
            "This file could not be opened as a Word document. Save it as .docx (not the older .doc) and try again."
        ) from exc

    part = document.part
    styles = _Styles(document)
    parsed = ParsedDocument(title=(document.core_properties.title or "").strip() or None)

    # Pass 1: collect raw items so the heading fallback can look at the whole document.
    items: list[dict] = []

    def related_blob(rel_id: str | None):
        if not rel_id:
            return None, None
        try:
            target = part.related_parts[rel_id]
        except KeyError:
            return None, None
        return target.blob, getattr(target, "content_type", None)

    def collect_media(element) -> list[Block]:
        blocks: list[Block] = []
        for blip in element.iter("{%s}blip" % NS["a"]):
            if _has_fallback_ancestor(blip):
                continue
            blob, mime = related_blob(blip.get(R + "embed"))
            if blob:
                blocks.append(Block(kind="image", image=blob, mime=mime))
        if not blocks:
            for image_data in element.iter("{%s}imagedata" % NS["v"]):
                if _has_fallback_ancestor(image_data):
                    continue
                blob, mime = related_blob(image_data.get(R + "id"))
                if blob:
                    blocks.append(Block(kind="image", image=blob, mime=mime))
        for chart_ref in element.iter("{%s}chart" % NS["c"]):
            if _has_fallback_ancestor(chart_ref):
                continue
            blob, _ = related_blob(chart_ref.get(R + "id"))
            if not blob:
                continue
            try:
                chart = parse_chart_xml(blob)
            except Exception as exc:  # a broken chart must never sink the whole report
                parsed.warnings.append(f"A chart could not be read ({type(exc).__name__}).")
                continue
            if chart is not None:
                blocks.append(Block(kind="chart", text=chart.as_text(), image=chart.render_png(),
                                    mime="image/png", caption=chart.title, origin="chart"))
        return blocks

    def collect_links(element) -> list[str]:
        urls: list[str] = []
        for link in element.iter(W + "hyperlink"):
            rel_id = link.get(R + "id")
            if not rel_id:
                continue
            rel = part.rels.get(rel_id)
            target = getattr(rel, "target_ref", None) or getattr(rel, "_target", None)
            if isinstance(target, str) and target.startswith(("http://", "https://")):
                urls.append(target)
        return urls

    for element in _iter_body(document.element.body):
        if element.tag == W + "tbl":
            rows = _table_rows(element)
            media = collect_media(element)
            tbl_links = collect_links(element)
            if rows and len(rows) >= 2 and len(rows[0]) >= 2:
                items.append({"type": "table", "rows": rows, "links": tbl_links})
            else:  # a layout table: treat its cells as ordinary paragraphs
                for idx, cell in enumerate(c for row in rows for c in row if c):
                    items.append({"type": "p", "text": cell, "styled": 0, "pseudo": 0,
                                  "links": tbl_links if idx == 0 else []})
            items.extend({"type": "media", "block": b} for b in media)
            continue

        style_el = element.find(f"{W}pPr/{W}pStyle")
        style_id = style_el.get(W + "val", "") if style_el is not None else ""
        if style_id in styles.toc:
            continue
        text = _text_of(element)
        p_links = collect_links(element)
        outline = element.find(f"{W}pPr/{W}outlineLvl")
        styled_level = styles.level.get(style_id, 0)
        if outline is not None:
            value = int(outline.get(W + "val", "9"))
            styled_level = value + 1 if 0 <= value <= 8 else styled_level
        is_list = element.find(f"{W}pPr/{W}numPr") is not None or style_id in styles.list
        if text:
            if style_id in styles.title:
                if not parsed.title:
                    parsed.title = text
                items.append({"type": "title", "text": text})
            else:
                is_caption = style_id in styles.caption or bool(CAPTION_RE.match(text)) and len(text) <= 240
                items.append({
                    "type": "p", "text": text, "styled": 0 if is_caption else styled_level,
                    "pseudo": 0 if (is_caption or is_list) else _pseudo_heading_level(
                        text, _is_all_bold(element, styles, style_id)),
                    "list": is_list and not styled_level, "caption": is_caption, "links": p_links,
                })
        elif p_links:
            items.append({"type": "p", "text": " ".join(p_links), "styled": 0, "pseudo": 0, "links": p_links})
        items.extend({"type": "media", "block": b} for b in collect_media(element))
        for text_box in element.iter(W + "txbxContent"):
            if _has_fallback_ancestor(text_box):
                continue
            for inner in text_box.iter(W + "p"):
                inner_text = _text_of(inner)
                inner_links = collect_links(inner)
                if inner_text:
                    items.append({"type": "p", "text": inner_text, "styled": 0, "pseudo": 0, "links": inner_links})
                elif inner_links:
                    items.append({"type": "p", "text": " ".join(inner_links), "styled": 0, "pseudo": 0, "links": inner_links})

    # Pass 2: decide which heading signal to trust, then attach captions.
    styled_headings = sum(1 for i in items if i["type"] == "p" and i.get("styled"))
    use_pseudo = styled_headings < 3
    if use_pseudo and any(i["type"] == "p" and i.get("pseudo") for i in items):
        parsed.warnings.append("No heading styles found; section breaks were inferred from bold or numbered lines.")

    blocks = parsed.blocks
    pending_caption: str | None = None

    def flush_caption() -> None:
        nonlocal pending_caption
        if pending_caption:
            blocks.append(Block(kind="paragraph", text=pending_caption))
            pending_caption = None

    for item in items:
        kind = item["type"]
        if kind == "title":
            continue
        if kind == "table":
            caption, pending_caption = (pending_caption, None) if pending_caption else (None, None)
            blocks.append(Block(kind="table", text=rows_to_markdown(item["rows"]), caption=caption,
                                links=list(item.get("links") or [])))
            continue
        if kind == "media":
            block: Block = item["block"]
            if pending_caption and not block.caption:
                block.caption, pending_caption = pending_caption, None
            blocks.append(block)
            continue
        text = item["text"]
        item_links = list(item.get("links") or [])
        if item.get("caption"):
            previous = blocks[-1] if blocks else None
            wants_table = text.lower().startswith("table")
            if previous is not None and not previous.caption and (
                (previous.kind in ("image", "chart") and not wants_table)
                or (previous.kind == "table" and wants_table)
            ):
                previous.caption = text
                if item_links:
                    previous.links.extend(item_links)
            else:
                flush_caption()
                pending_caption = text
            continue
        flush_caption()
        level = item.get("pseudo", 0) if use_pseudo else item.get("styled", 0)
        if level:
            blocks.append(Block(kind="heading", text=text, level=level, links=item_links))
        elif item.get("list"):
            blocks.append(Block(kind="list_item", text=text, links=item_links))
        else:
            blocks.append(Block(kind="paragraph", text=text, links=item_links))
    flush_caption()

    if not any(b.kind in ("paragraph", "list_item", "table") for b in blocks):
        raise UnreadableDocument("No readable text was found in this document.")
    return parsed
