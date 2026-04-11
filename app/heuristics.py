from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

# Section headers present in the coder prompt output format.
_SECTION_HEADERS = {
    "diagnosis:",
    "files changed:",
    "code search summary:",
    "fix explanation:",
    "test notes:",
}


@dataclass
class HeuristicEntry:
    issue_title: str
    fix_summary: str
    retries: int
    saved_at: str
    embedding: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def extract_fix_summary(coder_output: str) -> str:
    """Extract the 'Fix explanation:' section from coder stage output.

    Falls back to the first two non-empty lines when the section is absent.
    """
    lines = coder_output.splitlines()
    in_fix = False
    fix_lines: list[str] = []

    for line in lines:
        low = line.strip().lower()
        if low.startswith("fix explanation:"):
            in_fix = True
            rest = line.split(":", 1)[-1].strip()
            if rest:
                fix_lines.append(rest)
            continue
        if in_fix:
            if any(low.startswith(h) for h in _SECTION_HEADERS):
                break
            if line.strip():
                fix_lines.append(line.strip())

    if fix_lines:
        return " ".join(fix_lines)[:300]

    non_empty = [ln.strip() for ln in lines if ln.strip()]
    return " ".join(non_empty[:2])[:300]


# ---------------------------------------------------------------------------
# Similarity helpers
# ---------------------------------------------------------------------------

def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _keyword_sim(a: str, b: str) -> float:
    """Jaccard similarity on word tokens — used as embedding fallback."""
    ta = set(a.lower().split())
    tb = set(b.lower().split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _keyword_fallback(
    entries: list[HeuristicEntry],
    query: str,
    exclude_keys: set[str],
    k: int,
) -> list[HeuristicEntry]:
    scored = [
        (e, _keyword_sim(query, f"{e.issue_title} {e.fix_summary}"))
        for e in entries
        if e.saved_at not in exclude_keys
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [e for e, _ in scored[:k]]


# ---------------------------------------------------------------------------
# Embedding API
# ---------------------------------------------------------------------------

def _call_embedding(
    text: str,
    api_key: str,
    base_url: str,
    model: str,
) -> list[float]:
    """Call an OpenAI-compatible embedding endpoint and return the vector."""
    from openai import OpenAI  # already installed as a transitive dependency

    client = OpenAI(api_key=api_key, base_url=base_url)
    response = client.embeddings.create(model=model, input=text)
    return response.data[0].embedding


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _load_entries(path: Path) -> list[HeuristicEntry]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [HeuristicEntry(**item) for item in raw]
    except Exception as exc:
        LOGGER.warning("Failed to load heuristics from %s: %s", path, exc)
        return []


def _save_entries(path: Path, entries: list[HeuristicEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([e.to_dict() for e in entries], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def save_heuristic(
    *,
    issue_title: str,
    coder_output: str,
    retries: int,
    saved_at: str,
    heuristics_path: Path,
    api_key: str | None,
    base_url: str | None,
    embedding_model: str | None,
) -> None:
    """Extract a repair rule from a successful attempt and append it to heuristics.json.

    Attempts to compute and store an embedding for later semantic search.
    Silently skips the embedding when the API call fails so that the heuristic
    is still saved and available for keyword-based fallback retrieval.
    """
    fix_summary = extract_fix_summary(coder_output)
    if not fix_summary:
        LOGGER.debug("No fix summary extracted for '%s'; skipping heuristic save.", issue_title)
        return

    embedding: list[float] = []
    if api_key and base_url and embedding_model:
        try:
            embedding = _call_embedding(
                text=f"{issue_title}\n{fix_summary}",
                api_key=api_key,
                base_url=base_url,
                model=embedding_model,
            )
        except Exception as exc:
            LOGGER.warning(
                "Embedding call failed when saving heuristic for '%s': %s", issue_title, exc
            )

    entries = _load_entries(heuristics_path)
    entries.append(
        HeuristicEntry(
            issue_title=issue_title,
            fix_summary=fix_summary,
            retries=retries,
            saved_at=saved_at,
            embedding=embedding,
        )
    )
    _save_entries(heuristics_path, entries)
    LOGGER.info("Saved heuristic for '%s' to %s", issue_title, heuristics_path)


def load_heuristics_block(
    *,
    issue_title: str,
    issue_description: str,
    heuristics_path: Path,
    api_key: str | None,
    base_url: str | None,
    embedding_model: str | None,
    recent_k: int = 5,
    semantic_k: int = 3,
) -> str:
    """Build a prompt block combining recent and semantically similar heuristics.

    Strategy:
      - Always include the most recent ``recent_k`` entries (recency signal).
      - Search the full corpus for ``semantic_k`` entries most similar to the
        current issue using embedding cosine similarity when vectors are
        available, falling back to keyword Jaccard similarity otherwise.
      - Deduplicate by ``saved_at`` before rendering.

    Returns an empty string when no heuristics exist yet.
    """
    entries = _load_entries(heuristics_path)
    if not entries:
        return ""

    recent = entries[-recent_k:]
    recent_keys = {e.saved_at for e in recent}

    query = f"{issue_title}\n{issue_description}"
    semantic: list[HeuristicEntry] = []
    has_embeddings = any(e.embedding for e in entries)

    if api_key and base_url and embedding_model and has_embeddings:
        try:
            query_vec = _call_embedding(
                text=query,
                api_key=api_key,
                base_url=base_url,
                model=embedding_model,
            )
            scored = [
                (e, _cosine(query_vec, e.embedding))
                for e in entries
                if e.embedding and e.saved_at not in recent_keys
            ]
            scored.sort(key=lambda x: x[1], reverse=True)
            semantic = [e for e, _ in scored[:semantic_k]]
        except Exception as exc:
            LOGGER.warning(
                "Embedding query failed, falling back to keyword match: %s", exc
            )
            semantic = _keyword_fallback(entries, query, recent_keys, semantic_k)
    else:
        semantic = _keyword_fallback(entries, query, recent_keys, semantic_k)

    # semantic first (relevance), then recent (recency); deduplicate by saved_at
    seen: set[str] = set()
    merged: list[HeuristicEntry] = []
    for e in semantic + recent:
        if e.saved_at not in seen:
            seen.add(e.saved_at)
            merged.append(e)

    if not merged:
        return ""

    lines = ["[Repair heuristics from past successful fixes]"]
    for e in merged:
        retry_note = f"({e.retries} {'retry' if e.retries == 1 else 'retries'})"
        lines.append(f"- {e.issue_title}: {e.fix_summary} {retry_note}")
    return "\n".join(lines)
