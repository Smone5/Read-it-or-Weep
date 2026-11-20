"""Format-neutral document model.

Every parser (Word today; PDF, PowerPoint and plain text alongside it) turns a file
into the same flat list of ``Block`` objects in reading order. Everything after
parsing – sections, chunks, images, questions – works on this model only, which is
what makes adding a new document type a matter of writing one parser.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

BlockKind = Literal["heading", "paragraph", "list_item", "table", "image", "chart"]


@dataclass
class Block:
    kind: BlockKind
    text: str = ""                 # paragraph text, table as markdown, chart data as text
    level: int = 0                 # heading level (1 = top)
    image: bytes | None = None     # raw image bytes for kind == "image" / "chart"
    mime: str | None = None
    caption: str | None = None     # caption for a table / image / chart
    origin: str = "embedded"       # embedded | chart | page


@dataclass
class ParsedDocument:
    title: str | None = None
    blocks: list[Block] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class Section:
    ordinal: int
    level: int
    heading: str
    word_count: int = 0


@dataclass
class Chunk:
    ordinal: int
    section: int                   # index into sections
    kind: Literal["text", "table", "chart"]
    heading_path: str
    text: str
    token_count: int = 0


@dataclass
class ImageItem:
    ordinal: int
    section: int
    data: bytes
    mime: str
    width: int
    height: int
    sha256: str
    origin: str
    caption: str | None
    context: str                   # nearby text, so the model knows what the picture is about


@dataclass
class StructuredDocument:
    title: str
    sections: list[Section]
    chunks: list[Chunk]
    images: list[ImageItem]
    warnings: list[str]
    word_count: int
