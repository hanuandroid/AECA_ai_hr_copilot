"""Deterministic, structure-aware policy chunking.

Document -> sections (markdown headings) -> paragraph blocks -> packed chunks <= chunk_size.
A block is only split when it alone exceeds chunk_size: first at line (list item) boundaries,
then sentence boundaries, then words - so a chunk never ends half-way through a rule unless a
single sentence is longer than the chunk size. When a section needs several chunks, the last
block of the previous chunk is repeated (if it fits in `overlap` chars) to keep context.
"""

import hashlib
import re

from app.services.ai.rag.models import PolicyChunkData, PolicyDocument

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
DEFAULT_SECTION = "Overview"


def chunk_document(document: PolicyDocument, chunk_size: int, overlap: int) -> list[PolicyChunkData]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be >= 0 and < chunk_size")

    key = index_key(document.checksum, chunk_size, overlap)
    chunks: list[PolicyChunkData] = []
    for section, blocks in _sections(document.text):
        pieces = [piece for block in blocks for piece in _atomise(block, chunk_size)]
        for text in _pack(pieces, chunk_size, overlap):
            index = len(chunks)
            chunks.append(
                PolicyChunkData(
                    chunk_id=_chunk_id(document.policy_id, index, text),
                    policy_id=document.policy_id,
                    policy_title=document.title,
                    category=document.category,
                    source_filename=document.source_filename,
                    section=section,
                    chunk_index=index,
                    text=text,
                    index_key=key,
                )
            )
    return chunks


def index_key(document_checksum: str, chunk_size: int, overlap: int) -> str:
    """Identifies an indexed version of a document: changes when text OR chunking parameters change."""
    return hashlib.sha256(f"{document_checksum}|{chunk_size}|{overlap}".encode("utf-8")).hexdigest()


def _chunk_id(policy_id: int, index: int, text: str) -> str:
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return hashlib.sha256(f"{policy_id}|{index}|{text_hash}".encode("utf-8")).hexdigest()[:32]


def _sections(text: str) -> list[tuple[str, list[str]]]:
    sections: list[tuple[str, list[str]]] = []
    h2: str | None = None
    label = DEFAULT_SECTION
    blocks: list[str] = []
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append("\n".join(paragraph).strip())
            paragraph.clear()

    def flush_section() -> None:
        flush_paragraph()
        if blocks:
            sections.append((label, list(blocks)))
            blocks.clear()

    for line in text.split("\n"):
        heading = _HEADING_RE.match(line)
        if heading:
            level, title = len(heading.group(1)), heading.group(2).strip()
            if level == 1:
                flush_paragraph()  # document title: kept as policy_title, not a section
                continue
            flush_section()
            if level == 2:
                h2 = title
                label = title
            else:
                label = f"{h2} > {title}" if h2 else title
            continue
        if not line.strip():
            flush_paragraph()
        else:
            paragraph.append(line.strip())
    flush_section()
    return sections


def _atomise(block: str, size: int) -> list[str]:
    if len(block) <= size:
        return [block]
    lines = [line for line in block.split("\n") if line.strip()]
    if len(lines) > 1:
        return _group([piece for line in lines for piece in _atomise(line, size)], size, "\n")
    sentences = [s for s in _SENTENCE_RE.split(block) if s]
    if len(sentences) > 1:
        return _group([piece for sentence in sentences for piece in _atomise(sentence, size)], size, " ")
    return _hard_split(block, size)


def _group(parts: list[str], size: int, joiner: str) -> list[str]:
    grouped: list[str] = []
    current = ""
    for part in parts:
        candidate = f"{current}{joiner}{part}" if current else part
        if current and len(candidate) > size:
            grouped.append(current)
            current = part
        else:
            current = candidate
    if current:
        grouped.append(current)
    return grouped


def _hard_split(text: str, size: int) -> list[str]:
    words, parts, current = text.split(), [], ""
    for word in words:
        while len(word) > size:  # pathological token (e.g. a URL): split mid-word
            if current:
                parts.append(current)
                current = ""
            parts.append(word[:size])
            word = word[size:]
        candidate = f"{current} {word}" if current else word
        if current and len(candidate) > size:
            parts.append(current)
            current = word
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def _pack(pieces: list[str], size: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    for piece in pieces:
        if current and len("\n\n".join([*current, piece])) > size:
            chunks.append("\n\n".join(current))
            carry = current[-1]
            if 0 < len(carry) <= overlap and len(carry) + 2 + len(piece) <= size:
                current = [carry, piece]
            else:
                current = [piece]
        else:
            current.append(piece)
    if current:
        chunks.append("\n\n".join(current))
    return chunks
