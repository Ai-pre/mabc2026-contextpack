"""Bounded, text-only normalization of uploaded project documents."""

from __future__ import annotations

import io
import json
import math
import re
import zipfile
from pathlib import PurePosixPath
from typing import Any


MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_BODY_CHARS = 500_000
MAX_DOCX_UNPACKED_BYTES = 50 * 1024 * 1024
MAX_DOCX_ENTRIES = 2_000
MAX_PDF_PAGES = 500
MAX_PDF_STREAM_BYTES = 10 * 1024 * 1024
MAX_PDF_TOTAL_STREAM_BYTES = 50 * 1024 * 1024
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 20_000
_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".json": "application/json",
}


class DocumentParseError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def validate_filename(filename: str) -> str:
    """Accept browser path prefixes, retaining only a valid display basename."""
    if not isinstance(filename, str) or any(ord(char) < 32 or ord(char) == 127 for char in filename):
        raise DocumentParseError("A valid document filename is required.", 400)
    filename = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if (not filename or filename in {".", ".."} or len(filename) > 255
            or any(char in '<>:"|?*' for char in filename)):
        raise DocumentParseError("A valid document filename is required.", 400)
    try:
        filename.encode("utf-8")
    except UnicodeError as exc:
        raise DocumentParseError("A valid document filename is required.", 400) from exc
    if PurePosixPath(filename).suffix.lower() not in _MIME_TYPES:
        raise DocumentParseError("Supported document types are PDF, DOCX, TXT, MD and JSON.", 415)
    return filename


def _normalize_text(text: str) -> str:
    if "\x00" in text:
        raise DocumentParseError("Document text contains unsupported null characters.")
    try:
        text.encode("utf-8")
    except UnicodeError as exc:
        raise DocumentParseError("Document text contains invalid Unicode characters.") from exc
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) > MAX_BODY_CHARS:
        raise DocumentParseError("Extracted document text exceeds the 500,000-character limit.", 413)
    return text


class _Body:
    def __init__(self):
        self.parts: list[str] = []
        self.length = 0

    def append(self, text: str, *, separator: str = "\n\n") -> tuple[int, int]:
        text = _normalize_text(text)
        if not text:
            return self.length, self.length
        prefix = separator if self.parts else ""
        start = self.length + len(prefix)
        end = start + len(text)
        if end > MAX_BODY_CHARS:
            raise DocumentParseError("Extracted document text exceeds the 500,000-character limit.", 413)
        self.parts.extend((prefix, text))
        self.length = end
        return start, end

    def text(self) -> str:
        return "".join(self.parts)


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig", errors="strict")
    except UnicodeError as exc:
        raise DocumentParseError("Text documents must use UTF-8 encoding.") from exc


def _parse_text(raw: bytes, suffix: str) -> tuple[str, dict]:
    body = _normalize_text(_decode(raw))
    return body, {
        "encoding": "utf-8",
        "line_count": len(body.splitlines()),
        "blocks": [{"kind": "markdown" if suffix == ".md" else "text", "start": 0, "end": len(body)}],
    }


def _parse_pdf(raw: bytes) -> tuple[str, dict]:
    from pypdf import PdfReader
    from pypdf.generic import ArrayObject

    if not raw.lstrip().startswith(b"%PDF-"):
        raise DocumentParseError("The file is not a valid PDF document.")
    reader = PdfReader(io.BytesIO(raw), strict=True)
    if reader.is_encrypted:
        raise DocumentParseError("Encrypted PDF documents are not supported. Upload an unencrypted copy.")
    if len(reader.pages) > MAX_PDF_PAGES:
        raise DocumentParseError(f"PDF documents may contain at most {MAX_PDF_PAGES} pages.", 413)
    body = _Body()
    pages = []
    blank_pages = []
    total_stream_bytes = 0
    for number, page in enumerate(reader.pages, start=1):
        contents = page.get("/Contents")
        if contents is not None:
            contents = contents.get_object()
            streams = contents if isinstance(contents, ArrayObject) else [contents]
            page_stream_bytes = 0
            for stream in streams:
                data = stream.get_object().get_data()
                page_stream_bytes += len(data)
                total_stream_bytes += len(data)
                if (page_stream_bytes > MAX_PDF_STREAM_BYTES
                        or total_stream_bytes > MAX_PDF_TOTAL_STREAM_BYTES):
                    raise DocumentParseError("PDF content streams exceed the extraction size limit.", 413)
        text = _normalize_text(page.extract_text() or "")
        start, end = body.append(text)
        pages.append({"page": number, "start": start, "end": end, "has_text": bool(text)})
        if not text:
            blank_pages.append(number)
    metadata: dict[str, Any] = {"page_count": len(pages), "pages": pages, "ocr_performed": False}
    if blank_pages:
        metadata["warnings"] = [{
            "code": "pdf_pages_without_text",
            "pages": blank_pages,
            "message": "Some PDF pages contain no extractable text. OCR is not available.",
        }]
    return body.text(), metadata


def _parse_docx(raw: bytes) -> tuple[str, dict]:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    with zipfile.ZipFile(io.BytesIO(raw)) as package:
        entries = package.infolist()
        if (len(entries) > MAX_DOCX_ENTRIES
                or sum(entry.file_size for entry in entries) > MAX_DOCX_UNPACKED_BYTES):
            raise DocumentParseError("DOCX package exceeds the unpacked size limit.", 413)
        names = [entry.filename for entry in entries]
        if len(names) != len(set(names)):
            raise DocumentParseError("DOCX package contains duplicate entries.")
        if "word/document.xml" not in names or "[Content_Types].xml" not in names:
            raise DocumentParseError("The file is not a valid DOCX document.")

    document = Document(io.BytesIO(raw))
    body = _Body()
    blocks = []

    def table_text(table: Table, depth: int = 0) -> str:
        if depth > 32:
            raise DocumentParseError("DOCX tables exceed the nesting limit.", 413)
        rows = []
        length = 0
        for row in table.rows:
            cells = []
            # Merged cells can appear more than once in row.cells; retain each physical cell once.
            seen_cells: set[Any] = set()
            for cell in row.cells:
                if cell._tc in seen_cells:
                    continue
                seen_cells.add(cell._tc)
                parts = []
                for item in cell.iter_inner_content():
                    parts.append(item.text if isinstance(item, Paragraph) else table_text(item, depth + 1))
                cells.append("\n".join(parts))
            text = "\t".join(cells)
            length += len(text) + 1
            if length > MAX_BODY_CHARS:
                raise DocumentParseError("Extracted document text exceeds the 500,000-character limit.", 413)
            rows.append(text)
        return "\n".join(rows)

    for index, item in enumerate(document.iter_inner_content()):
        if isinstance(item, Paragraph):
            style = item.style.name if item.style else ""
            heading = re.fullmatch(r"Heading (\d+)", style, re.IGNORECASE)
            block: dict[str, Any] = {"index": index, "kind": "heading" if heading else "paragraph", "style": style}
            if heading:
                block["level"] = int(heading.group(1))
            text = item.text
        else:
            block = {"index": index, "kind": "table", "rows": len(item.rows), "columns": len(item.columns)}
            text = table_text(item)
        start, end = body.append(text)
        block.update(start=start, end=end)
        blocks.append(block)
    return body.text(), {"blocks": blocks, "block_count": len(blocks)}


def _parse_json(raw: bytes) -> tuple[str, dict]:
    def pairs(items: list[tuple[str, Any]]) -> dict:
        result = {}
        for key, value in items:
            if key in result:
                raise DocumentParseError("JSON objects may not contain duplicate keys.")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise DocumentParseError(f"JSON contains an invalid numeric constant: {value}.")

    value = json.loads(_decode(raw), object_pairs_hook=pairs, parse_constant=reject_constant)
    body = _Body()
    blocks = []
    nodes = 0

    def visit(item: Any, path: str, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if depth > MAX_JSON_DEPTH or nodes > MAX_JSON_NODES:
            raise DocumentParseError("JSON exceeds the nesting or item-count limit.", 413)
        if isinstance(item, dict):
            for key, child in item.items():
                segment = "." + key if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) else "[" + json.dumps(key, ensure_ascii=False) + "]"
                if len(path) + len(segment) > MAX_BODY_CHARS:
                    raise DocumentParseError("JSON key paths exceed the extraction size limit.", 413)
                visit(child, path + segment, depth + 1)
        elif isinstance(item, list):
            for index, child in enumerate(item):
                visit(child, f"{path}[{index}]", depth + 1)
        else:
            if isinstance(item, float) and not math.isfinite(item):
                raise DocumentParseError("JSON numbers must be finite.")
            text = item if isinstance(item, str) else json.dumps(item, allow_nan=False)
            text = _normalize_text(text)
            if not text:
                return
            start, end = body.append(f"{path}: {text}", separator="\n")
            blocks.append({"kind": "json_value", "path": path, "start": start, "end": end})

    visit(value, "$", 0)
    return body.text(), {"blocks": blocks, "node_count": nodes}


def parse_document(filename: str, raw: bytes) -> dict:
    filename = validate_filename(filename)
    if not isinstance(raw, bytes) or not raw:
        raise DocumentParseError("The uploaded document is empty.", 400)
    if len(raw) > MAX_FILE_BYTES:
        raise DocumentParseError("Documents must be no larger than 10 MiB.", 413)
    suffix = PurePosixPath(filename).suffix.lower()
    try:
        if suffix in {".txt", ".md"}:
            body, metadata = _parse_text(raw, suffix)
        elif suffix == ".pdf":
            body, metadata = _parse_pdf(raw)
        elif suffix == ".docx":
            body, metadata = _parse_docx(raw)
        else:
            body, metadata = _parse_json(raw)
    except DocumentParseError:
        raise
    except RecursionError as exc:
        raise DocumentParseError("Document exceeds the supported nesting limit.", 413) from exc
    except Exception as exc:
        raise DocumentParseError(f"The {suffix[1:].upper()} document could not be parsed.") from exc
    if not body.strip():
        message = "The document contains no extractable text."
        if suffix == ".pdf":
            message += " Scanned PDFs require OCR, which is not available."
        raise DocumentParseError(message)
    return {
        "body": body,
        "title": filename,
        "timestamp": None,
        "metadata": {
            **metadata,
            "original_filename": filename,
            "mime_type": _MIME_TYPES[suffix],
            "size_bytes": len(raw),
            "character_count": len(body),
            "offset_unit": "unicode_code_points",
        },
    }
