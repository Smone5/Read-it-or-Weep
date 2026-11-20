"""Document ingestion: file bytes -> sections, chunks and pictures.

To support a new document type, write a function ``(data: bytes, filename: str) ->
ParsedDocument`` and register its extensions in ``PARSERS`` below. Nothing else changes.
"""
from __future__ import annotations

from pathlib import PurePath
from typing import Callable

from ingest.docx_parser import UnreadableDocument, parse_docx
from ingest.model import ParsedDocument, StructuredDocument
from ingest.other_parsers import parse_pdf, parse_pptx, parse_text
from ingest.structure import build_structure

Parser = Callable[[bytes, str], ParsedDocument]

PARSERS: dict[str, Parser] = {
    ".docx": parse_docx,
    ".pdf": parse_pdf,
    ".pptx": parse_pptx,
    ".txt": parse_text,
    ".md": parse_text,
}

MIME_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
    ".md": "text/markdown",
}


def supported_extensions() -> list[str]:
    return sorted(PARSERS)


def extension_of(filename: str) -> str:
    return PurePath(filename).suffix.lower()


def parse_document(data: bytes, filename: str) -> StructuredDocument:
    extension = extension_of(filename)
    parser = PARSERS.get(extension)
    if parser is None:
        if extension == ".doc":
            raise UnreadableDocument("Old-format .doc files are not supported. Open it in Word and save as .docx.")
        raise UnreadableDocument(
            f"Unsupported file type '{extension or filename}'. Supported: {', '.join(supported_extensions())}.")
    return build_structure(parser(data, filename), filename)


__all__ = ["parse_document", "supported_extensions", "extension_of", "UnreadableDocument", "MIME_TYPES"]
