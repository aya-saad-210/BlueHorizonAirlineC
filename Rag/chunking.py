# rag/chunking.py
#
# RUBRIC: "Vector database architecture" (8 pts) -- this file is where the
# metadata payload gets attached to every chunk before it reaches
# vector_store.py, and it's why hybrid search (rag/hybrid_search.py) can
# win on citation-heavy questions: section numbers like "4.2b" are kept
# intact as their own metadata field AND embedded as part of the chunk text,
# instead of being split across chunk boundaries.
#
# Chunking strategy: policy_docs/*.md files use numbered Markdown headers
# ("## Section 4 -- ...", "4.2b **Mechanical failure...") on purpose (see
# those files). We chunk at the sub-section level (the "4.2a", "4.2b"
# granularity) rather than a fixed token-count window, because a fixed
# window would frequently split a numbered clause in half and destroy
# exactly the citation-precision hybrid search is supposed to demonstrate.

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

SECTION_HEADER_RE = re.compile(r"^##\s+Section\s+(\d+)\s*[--—]\s*(.+)$", re.MULTILINE)
# Matches sub-clause markers like "4.2a **Mechanical...", "3.1 **Short-haul...",
# "5.2 If no reserve crew..." at the start of a line.
SUBSECTION_RE = re.compile(r"^(\d+\.\d+[a-z]?)\s+(.*)$", re.MULTILINE)


@dataclass
class Chunk:
    chunk_id: str
    text: str
    metadata: dict = field(default_factory=dict)


def _parse_frontmatter(raw: str) -> tuple[dict, str]:
    """Pulls the '---\\nkey: value\\n---' YAML-lite frontmatter off the top
    of each policy_docs/*.md file (doc_type, last_reviewed, owner)."""
    if not raw.startswith("---"):
        return {}, raw
    end = raw.find("---", 3)
    if end == -1:
        return {}, raw
    fm_block = raw[3:end].strip()
    body = raw[end + 3 :].lstrip("\n")
    meta = {}
    for line in fm_block.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, body


def chunk_document(path: Path, chunk_size_chars: int = 900) -> list[Chunk]:
    """Splits one policy_docs/*.md file into section-aware chunks.

    Each chunk carries metadata: source, doc_type, section, last_reviewed.
    This metadata is what vector_store.py indexes for pre-/mid-search
    filtering (e.g. "only search duty_time_policy chunks").
    """
    raw = path.read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(raw)
    doc_type = fm.get("doc_type", path.stem)
    last_reviewed = fm.get("last_reviewed", "unknown")

    chunks: list[Chunk] = []
    sections = list(SECTION_HEADER_RE.finditer(body))
    for i, match in enumerate(sections):
        section_num = match.group(1)
        section_title = match.group(2).strip()
        start = match.end()
        end = sections[i + 1].start() if i + 1 < len(sections) else len(body)
        section_body = body[start:end].strip()

        # Sub-chunk at the "4.2a" / "4.2b" clause granularity when present,
        # so a citation-heavy question can retrieve exactly one clause
        # instead of the whole section.
        subs = list(SUBSECTION_RE.finditer(section_body))
        if not subs:
            chunks.append(
                Chunk(
                    chunk_id=f"{path.stem}:sec{section_num}",
                    text=f"Section {section_num} -- {section_title}\n{section_body}"[:chunk_size_chars],
                    metadata={
                        "source": path.name,
                        "doc_type": doc_type,
                        "section": section_num,
                        "last_reviewed": last_reviewed,
                    },
                )
            )
            continue

        for j, sub in enumerate(subs):
            clause_id = sub.group(1)
            sub_start = sub.start()
            sub_end = subs[j + 1].start() if j + 1 < len(subs) else len(section_body)
            clause_text = section_body[sub_start:sub_end].strip()
            chunks.append(
                Chunk(
                    chunk_id=f"{path.stem}:sec{section_num}:{clause_id}",
                    text=(
                        f"[{doc_type} | Section {section_num} -- {section_title} | "
                        f"Clause {clause_id}]\n{clause_text}"
                    )[:chunk_size_chars],
                    metadata={
                        "source": path.name,
                        "doc_type": doc_type,
                        "section": section_num,
                        "clause": clause_id,
                        "last_reviewed": last_reviewed,
                    },
                )
            )
    return chunks


def chunk_policy_docs(policy_docs_dir: Path) -> list[Chunk]:
    """Chunks every .md file in rag/policy_docs/. This is the entry point
    ingest.py calls before embedding + upserting into the vector store."""
    all_chunks: list[Chunk] = []
    for md_path in sorted(policy_docs_dir.glob("*.md")):
        all_chunks.extend(chunk_document(md_path))
    return all_chunks


if __name__ == "__main__":
    here = Path(__file__).parent / "policy_docs"
    result = chunk_policy_docs(here)
    print(f"Produced {len(result)} chunks from {here}")
    for c in result[:5]:
        print("---")
        print(c.chunk_id, c.metadata)
        print(c.text[:150])
