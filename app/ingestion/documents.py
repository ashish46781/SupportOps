from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from pathlib import Path

from pypdf import PdfReader

from app.models import EvidenceItem
from app.stores.qdrant import content_hash


def document_source_id(path: Path) -> str:
    if path.stem.startswith("INC-"):
        return path.stem.split("_")[0]
    return path.stem


def extract_pdf(
    path: Path,
    category: str | None = None,
    service: str | None = None,
    *,
    use_docling: bool = True,
) -> list[EvidenceItem]:
    """Extract PDF chunks with page numbers and optional Docling headings."""
    headings = _docling_headings(path) if use_docling else {}
    reader = PdfReader(path)
    source_id = document_source_id(path)
    source_hash = file_sha256(path)
    chunks: list[EvidenceItem] = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = _normalize(page.extract_text() or "")
        if not text:
            continue
        blocks = _semantic_blocks(text)
        current_section = headings.get(page_number)
        for index, block in enumerate(blocks, start=1):
            if _looks_like_heading(block):
                current_section = block
                continue
            chunk_id = f"{source_id}:p{page_number}:c{index}"
            chunks.append(
                EvidenceItem(
                    citation_id=f"[{path.name}, p.{page_number}]"
                    if not source_id.startswith("INC-")
                    else f"[{source_id}, p.{page_number}]",
                    chunk_id=chunk_id,
                    source_id=source_id,
                    source_path=path.as_posix(),
                    source_type="incident" if source_id.startswith("INC-") else "document",
                    page=page_number,
                    section=current_section,
                    text=block,
                    parent_id=f"{source_id}:p{page_number}:context{(index - 1) // 3 + 1}",
                    parent_text="\n\n".join(
                        blocks[((index - 1) // 3) * 3 : ((index - 1) // 3) * 3 + 3]
                    ),
                    source_hash=source_hash,
                    entity_ids=_entity_ids(block),
                    ticket_category=category,
                    service=service,
                    content_hash=content_hash(block),
                )
            )
    return chunks


def _docling_headings(path: Path) -> dict[int, str]:
    try:
        from docling.document_converter import DocumentConverter

        document = DocumentConverter().convert(path).document
        headings: dict[int, str] = {}
        for item, _level in document.iterate_items():
            label = str(getattr(item, "label", "")).lower()
            text = getattr(item, "text", "")
            provenance = getattr(item, "prov", None) or []
            if "section" in label and text and provenance:
                page_number = int(getattr(provenance[0], "page_no", 1))
                headings.setdefault(page_number, text.strip())
        return headings
    except Exception:
        # PyPDF can still extract page text if Docling fails.
        return {}


def _semantic_blocks(text: str, max_words: int = 180) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    output: list[str] = []
    for paragraph in paragraphs:
        lines = [line.strip() for line in paragraph.splitlines() if line.strip()]
        # Attach the table header to each row so it makes sense on its own.
        if any(" | " in line for line in lines):
            header = next((line for line in lines if " | " in line), "")
            for line in lines:
                if " | " in line and line != header:
                    output.append(f"Table columns: {header}. Row: {line}")
            non_table = " ".join(line for line in lines if " | " not in line)
            if non_table:
                output.append(non_table)
            continue
        words = paragraph.split()
        for start in range(0, len(words), max_words):
            output.append(" ".join(words[start : start + max_words]))
    return output or [text]


def _normalize(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text.replace("\r", "\n")).strip()


def _looks_like_heading(text: str) -> bool:
    return len(text.split()) <= 10 and text.rstrip().endswith(":")


def _entity_ids(text: str) -> list[str]:
    patterns = [r"\b(?:INC|TKT|CUST|ORD|PAY)-\d{3,4}\b", r"\b[A-Z][A-Z0-9]+_[A-Z0-9_]+\b"]
    return sorted({match for pattern in patterns for match in re.findall(pattern, text)})


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def allowed_source(path: Path, roots: Iterable[Path], extensions: set[str]) -> Path:
    resolved = path.resolve()
    if resolved.suffix.lower() not in extensions:
        raise ValueError(f"Unsupported file extension: {resolved.suffix}")
    if not any(resolved.is_relative_to(root.resolve()) for root in roots):
        raise ValueError("Source path is outside configured directories")
    if resolved.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("Source exceeds the 10 MB ingestion limit")
    return resolved
