"""Turn a parsed document into sections, chunks and usable pictures.

Sections are the topic-sized slices players "clear" in the game, so the aim is a
handful to a dozen of them whatever the document's heading habits are. Chunks are
the passages questions are written from; tables and charts get chunks of their own.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from ingest.images import CleanImage, clean_image
from ingest.model import Block, Chunk, ImageItem, ParsedDocument, Section, StructuredDocument
from services.video import extract_youtube_urls

TARGET_TOKENS = 360
MAX_TOKENS = 520
MIN_TAIL_TOKENS = 70
TABLE_MAX_TOKENS = 700
MAX_SECTIONS = 14
TINY_SECTION_WORDS = 35
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def approx_tokens(text: str) -> int:
    """Good-enough token estimate; avoids downloading a tokenizer at runtime."""
    return max(1, round(len(text) / 4))


def _words(text: str) -> int:
    return len(text.split())


@dataclass
class _Draft:
    heading: str
    level: int
    blocks: list[Block] = field(default_factory=list)

    @property
    def words(self) -> int:
        return sum(_words(b.text) for b in self.blocks if b.kind in ("paragraph", "list_item", "table", "chart"))

    @property
    def has_media(self) -> bool:
        return any(b.kind in ("table", "image", "chart") or bool(b.links) for b in self.blocks)


def _topic_level(blocks: list[Block]) -> int:
    counts = Counter(b.level for b in blocks if b.kind == "heading" and b.level >= 1)
    if not counts:
        return 0
    running = 0
    chosen = max(counts)
    for level in sorted(counts):
        running += counts[level]
        if running >= 3:
            chosen = level
            break
    # A level that produces far too many topics: step back up if the shallower level is usable.
    while chosen > min(counts) and sum(c for lvl, c in counts.items() if lvl <= chosen) > MAX_SECTIONS * 2:
        shallower = sum(c for lvl, c in counts.items() if lvl < chosen)
        if shallower < 3:
            break
        chosen = max(lvl for lvl in counts if lvl < chosen)
    return chosen


_BOILERPLATE_HEADING = re.compile(
    r"^(table of contents|contents|list of (tables|figures|exhibits|charts|acronyms|abbreviations)|"
    r"acronyms|abbreviations|references|bibliography|works cited|acknowledg(e)?ments|index|keywords)$",
    re.I,
)


def _split_giant(draft: _Draft) -> list[_Draft]:
    """If cover-page font sizes caused a giant section to absorb real chapter headings, split on its internal headings."""
    sub_levels = Counter(b.level for b in draft.blocks if b.kind == "heading" and b.level >= 1)
    if not sub_levels:
        return [draft]
    # Pick the shallowest internal heading level that yields at least 2 real headings.
    split_lvl = None
    running = 0
    for lvl in sorted(sub_levels):
        running += sub_levels[lvl]
        if running >= 2:
            split_lvl = lvl
            break
    if split_lvl is None:
        return [draft]
    parts: list[_Draft] = []
    cur = _Draft(heading=draft.heading, level=draft.level)
    for block in draft.blocks:
        if block.kind == "heading" and 1 <= block.level <= split_lvl:
            if cur.blocks:
                parts.append(cur)
            cur = _Draft(heading=block.text.strip(), level=block.level)
        else:
            cur.blocks.append(block)
    if cur.blocks:
        parts.append(cur)
    return parts if len(parts) >= 2 else [draft]


def _draft_sections(parsed: ParsedDocument, fallback_title: str) -> list[_Draft]:
    blocks = parsed.blocks
    level = _topic_level(blocks)
    drafts: list[_Draft] = []
    current = _Draft(heading="Overview", level=1)
    for block in blocks:
        if block.kind == "heading" and level and 1 <= block.level <= level:
            if current.blocks:
                drafts.append(current)
            current = _Draft(heading=block.text.strip(), level=block.level)
        else:
            current.blocks.append(block)
    if current.blocks or not drafts:
        drafts.append(current)

    # Headings with nothing under them (e.g. "2. Finance" directly followed by "2.1 Revenue") are dropped.
    drafts = [d for d in drafts if d.blocks]

    # If a single section still swallows >45% of a large document and contains sub-headings, drill down into it
    # (up to two passes in case cover/callout sub-headers sit between L1 and the true chapter level).
    total_words = sum(d.words for d in drafts)
    if total_words >= 1200:
        for _ in range(2):
            expanded: list[_Draft] = []
            changed = False
            for draft in drafts:
                if draft.words >= 1200 and draft.words > total_words * 0.45:
                    pieces = _split_giant(draft)
                    if len(pieces) > 1:
                        changed = True
                    expanded.extend(pieces)
                else:
                    expanded.append(draft)
            drafts = expanded
            if not changed:
                break

    # Drop boilerplate front/back-matter (TOC, List of Tables/Figures, References) when real sections exist.
    substantive = [d for d in drafts if not _BOILERPLATE_HEADING.match(d.heading.strip())]
    if substantive:
        drafts = substantive

    # Drop empty / cover-art-only leading scraps (0-19 words and no media/tables/charts/links).
    if len(drafts) > 1:
        cleaned_drafts = [
            d for d in drafts
            if d.words >= 20 or d.has_media
        ]
        if cleaned_drafts:
            drafts = cleaned_drafts

    # Merge consecutive sections with identical headings (e.g. multi-page tables that repeat their title).
    deduped: list[_Draft] = []
    for draft in drafts:
        if deduped and re.sub(r"\s+", " ", deduped[-1].heading.lower()) == re.sub(r"\s+", " ", draft.heading.lower()):
            deduped[-1].blocks.extend(draft.blocks)
        else:
            deduped.append(draft)
    drafts = deduped

    if len(drafts) == 1 and not level:
        drafts = _split_evenly(drafts[0], fallback_title)

    # Fold tiny scraps into a neighbour, then cap the total by merging the smallest adjacent pairs.
    merged: list[_Draft] = []
    for draft in drafts:
        if merged and draft.words < TINY_SECTION_WORDS and not draft.has_media:
            merged[-1].blocks.append(Block(kind="heading", text=draft.heading, level=draft.level + 1))
            merged[-1].blocks.extend(draft.blocks)
        else:
            merged.append(draft)
    if len(merged) > 1 and merged[0].words < TINY_SECTION_WORDS and not merged[0].has_media:
        first = merged.pop(0)
        merged[0].blocks = first.blocks + merged[0].blocks
    while len(merged) > MAX_SECTIONS:
        index = min(range(len(merged) - 1), key=lambda i: merged[i].words + merged[i + 1].words)
        absorbed = merged.pop(index + 1)
        merged[index].blocks.append(Block(kind="heading", text=absorbed.heading, level=absorbed.level + 1))
        merged[index].blocks.extend(absorbed.blocks)
    return merged


def _split_evenly(draft: _Draft, title: str) -> list[_Draft]:
    """No headings at all: cut the text into a few even parts so progress can still be tracked."""
    total = draft.words
    parts = max(1, min(8, round(total / 650)))
    if parts == 1:
        return [_Draft(heading=title or "The report", level=1, blocks=draft.blocks)]
    target = total / parts
    result: list[_Draft] = []
    current = _Draft(heading="", level=1)
    for block in draft.blocks:
        current.blocks.append(block)
        if current.words >= target and len(result) < parts - 1:
            result.append(current)
            current = _Draft(heading="", level=1)
    if current.blocks:
        result.append(current)
    for i, part in enumerate(result, start=1):
        part.heading = f"Part {i} of {len(result)}"
    return result


def _split_long(text: str) -> list[str]:
    if approx_tokens(text) <= MAX_TOKENS:
        return [text]
    pieces: list[str] = []
    buffer = ""
    for sentence in _SENTENCE_SPLIT.split(text):
        if approx_tokens(sentence) > MAX_TOKENS:
            if buffer:
                pieces.append(buffer)
                buffer = ""
            # Fallback for unpunctuated blocks: split cleanly on word boundaries.
            words = sentence.split()
            chunk_words: list[str] = []
            for word in words:
                candidate = " ".join([*chunk_words, word])
                if chunk_words and approx_tokens(candidate) > TARGET_TOKENS:
                    pieces.append(" ".join(chunk_words))
                    chunk_words = [word]
                else:
                    chunk_words.append(word)
            if chunk_words:
                buffer = " ".join(chunk_words)
            continue
        if buffer and approx_tokens(buffer + " " + sentence) > TARGET_TOKENS:
            pieces.append(buffer)
            buffer = sentence
        else:
            buffer = f"{buffer} {sentence}".strip()
    if buffer:
        pieces.append(buffer)
    return pieces


def _split_table(markdown: str, caption: str | None) -> list[str]:
    lines = markdown.split("\n")
    header, rows = lines[:2], lines[2:]
    prefix = f"{caption}\n" if caption else ""
    if approx_tokens(markdown) <= TABLE_MAX_TOKENS or len(rows) < 4:
        return [prefix + markdown]
    parts: list[str] = []
    group: list[str] = []
    for row in rows:
        group.append(row)
        if approx_tokens("\n".join(header + group)) > TABLE_MAX_TOKENS * 0.8:
            parts.append(prefix + "\n".join(header + group))
            group = []
    if group:
        parts.append(prefix + "\n".join(header + group))
    return parts


def _prepare_images(parsed: ParsedDocument) -> tuple[dict[int, CleanImage], Counter[str]]:
    """Clean every picture up front and drop the unusable and the repeated (logos, template art).

    Returns usable pictures keyed by block index. Unusable picture blocks are removed from the
    document so they cannot make an otherwise empty section look like it has content.
    """
    skipped: Counter[str] = Counter()
    cleaned: dict[int, CleanImage] = {}
    for index, block in enumerate(parsed.blocks):
        if block.kind in ("image", "chart") and block.image:
            image, reason = clean_image(block.image, block.mime)
            if image is None:
                skipped[reason or "unusable"] += 1
            else:
                cleaned[index] = image
    repeats = Counter(image.sha256 for image in cleaned.values())
    for index in [i for i, image in cleaned.items() if repeats[image.sha256] > 1]:
        skipped["repeated picture (logo or template art)"] += 1
        del cleaned[index]

    kept: list[Block] = []
    usable: dict[int, CleanImage] = {}
    for index, block in enumerate(parsed.blocks):
        if block.kind == "image" and index not in cleaned:
            continue
        if index in cleaned:
            usable[id(block)] = cleaned[index]
        elif block.kind == "chart":
            block.image = None  # keep the chart's data as text even if its picture was unusable
        kept.append(block)
    parsed.blocks = kept
    return usable, skipped


_MARKER_LINE = re.compile(r"^\[[^\]]*\]$")


def build_structure(parsed: ParsedDocument, filename: str = "") -> StructuredDocument:
    fallback_title = parsed.title or re.sub(r"\.[A-Za-z0-9]+$", "", filename).replace("_", " ").strip() or "The report"
    usable_images, skipped = _prepare_images(parsed)
    drafts = _draft_sections(parsed, fallback_title)
    warnings = list(parsed.warnings)

    sections: list[Section] = []
    chunks: list[Chunk] = []
    images: list[ImageItem] = []

    for section_index, draft in enumerate(drafts):
        section_videos = extract_youtube_urls(
            "\n".join(filter(None, [b.text for b in draft.blocks] + [b.caption for b in draft.blocks])),
            [link for b in draft.blocks for link in b.links],
        )
        sections.append(Section(ordinal=section_index, level=draft.level, heading=draft.heading,
                                word_count=draft.words, video_urls=section_videos))
        subheading: str | None = None
        buffer: list[str] = []
        buffer_path: str | None = None
        section_chunk_start = len(chunks)
        recent_text: list[str] = []          # for picture context
        waiting_for_next_text: list[ImageItem] = []

        def path() -> str:
            return f"{draft.heading} › {subheading}" if subheading else draft.heading

        def add(text: str) -> None:
            nonlocal buffer_path
            if buffer_path is None:
                buffer_path = path()
            buffer.append(text)

        def flush() -> None:
            nonlocal buffer_path
            lines = list(buffer)
            buffer.clear()
            chunk_path, buffer_path = buffer_path, None
            while lines and (not lines[-1] or _MARKER_LINE.match(lines[-1])):
                lines.pop()   # a sub-heading with nothing under it yet belongs to what follows
            text = "\n".join(lines).strip()
            if text:
                chunks.append(Chunk(ordinal=len(chunks), section=section_index, kind="text",
                                    heading_path=chunk_path or path(), text=text, token_count=approx_tokens(text)))

        def buffered_tokens() -> int:
            return approx_tokens("\n".join(buffer)) if buffer else 0

        for block in draft.blocks:
            if block.kind == "heading":
                if buffered_tokens() >= 60:
                    flush()
                subheading = block.text.strip()
                add(f"[{subheading}]")
                continue
            if block.kind in ("paragraph", "list_item"):
                text = ("- " + block.text) if block.kind == "list_item" else block.text
                for piece in _split_long(text):
                    if buffer and approx_tokens("\n".join(buffer) + "\n" + piece) > MAX_TOKENS:
                        flush()
                    add(piece)
                    if buffered_tokens() >= TARGET_TOKENS and block.kind == "paragraph":
                        flush()
                recent_text.append(block.text)
                for image in waiting_for_next_text:
                    image.context = (image.context + "\nText after the picture: " + block.text[:400]).strip()
                waiting_for_next_text.clear()
                continue
            if block.kind == "table":
                flush()
                for part in _split_table(block.text, block.caption):
                    chunks.append(Chunk(ordinal=len(chunks), section=section_index, kind="table",
                                        heading_path=path(), text=part, token_count=approx_tokens(part)))
                recent_text.append(block.caption or "A table")
                continue
            if block.kind in ("image", "chart"):
                if block.kind == "chart" and block.text:
                    flush()
                    text = (f"{block.caption}\n" if block.caption and block.caption not in block.text else "") + block.text
                    chunks.append(Chunk(ordinal=len(chunks), section=section_index, kind="chart",
                                        heading_path=path(), text=text, token_count=approx_tokens(text)))
                cleaned = usable_images.get(id(block))
                if cleaned is None:
                    continue
                context_parts = [f"Section: {path()}"]
                if block.caption:
                    context_parts.append(f"Caption: {block.caption}")
                if recent_text:
                    context_parts.append("Text before the picture: " + " ".join(recent_text[-2:])[-600:])
                item = ImageItem(ordinal=len(images), section=section_index, data=cleaned.data, mime=cleaned.mime,
                                 width=cleaned.width, height=cleaned.height, sha256=cleaned.sha256,
                                 origin=block.origin, caption=block.caption, context="\n".join(context_parts))
                images.append(item)
                waiting_for_next_text.append(item)
        flush()

        # A short trailing passage reads better joined to the one before it.
        section_chunks = chunks[section_chunk_start:]
        if (len(section_chunks) >= 2 and section_chunks[-1].kind == "text" and section_chunks[-2].kind == "text"
                and section_chunks[-1].token_count < MIN_TAIL_TOKENS):
            tail = chunks.pop()
            chunks[-1].text = f"{chunks[-1].text}\n{tail.text}"
            chunks[-1].token_count = approx_tokens(chunks[-1].text)

    for index, chunk in enumerate(chunks):
        chunk.ordinal = index
    for reason, count in skipped.items():
        warnings.append(f"Skipped {count} picture{'s' if count != 1 else ''}: {reason}.")

    word_count = sum(s.word_count for s in sections)
    if word_count < 150:
        warnings.append("This document has very little text; expect only a few questions.")
    return StructuredDocument(title=fallback_title, sections=sections, chunks=chunks, images=images,
                              warnings=warnings, word_count=word_count)
