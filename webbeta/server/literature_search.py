"""MISSION literature-research Phase 1: the search_literature tool.

Loads the two pre-built collections (server/literature/collection_primary.{json,npy} = Harris +
Cartea/Jaimungal/Penalva merged; collection_hull.{json,npy} = Hull alone) and searches them via
cosine similarity against a query embedded through the same local nomic-embed-text-v1.5 model
used to build the index (a second llama-server instance, --embedding mode, port 8091 -- see
LITERATURE_RESEARCH_REPORT.md for why this model/approach).

Direct instruction: query Harris+Cartea (the "primary" collection) first; fall back to Hull ONLY
when a query has no good match there. Hull is never merged into the same search -- at 685k tokens
it's roughly half the corpus and the least relevant; merging would dilute every microstructure
query with derivatives-pricing noise.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import httpx
import numpy as np

LITERATURE_DIR = Path(__file__).resolve().parent / "literature"
EMBED_URL = "http://127.0.0.1:8091/v1/embeddings"

# MISSION literature-research: calibrated live against 7 real queries spanning clearly-primary
# (order flow toxicity, dealer inventory, optimal execution), clearly-Hull (Black-Scholes,
# binomial trees, futures margin mechanics), and one deliberately ambiguous query -- see the
# report's Phase 1 section for the actual numbers. First attempt used an ABSOLUTE cutoff on
# primary's own top score; wrong, discovered live -- nomic-embed's cosine scores cluster tightly
# (0.65-0.79 across ALL seven queries regardless of true relevance), so no absolute cutoff in
# that range is meaningful, and one low enough to ever fire would also never fire (every score
# measured was well above 0.55). The real, working signal is the DIRECT COMPARISON between
# primary's and Hull's own best scores -- cleanly separated by 0.06-0.09 for genuine microstructure
# queries (primary wins) and 0.07-0.13 for genuine pricing queries (Hull wins), with the one
# deliberately ambiguous query landing a bare 0.012 apart. FALLBACK_MARGIN requires Hull to beat
# primary by more than pure noise before deferring to it.
FALLBACK_MARGIN = 0.02

_cache: dict[str, tuple[list[dict], np.ndarray]] = {}


def _load_collection(name: str) -> tuple[list[dict], np.ndarray]:
    if name not in _cache:
        chunks = json.loads((LITERATURE_DIR / f"collection_{name}.json").read_text())
        embeddings = np.load(LITERATURE_DIR / f"collection_{name}.npy")
        _cache[name] = (chunks, embeddings)
    return _cache[name]


def embed_query(text: str) -> np.ndarray:
    resp = httpx.post(EMBED_URL, json={"input": [text]}, timeout=30.0)
    resp.raise_for_status()
    return np.array(resp.json()["data"][0]["embedding"], dtype=np.float32)


def _cosine_sim(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    q_norm = query_vec / (np.linalg.norm(query_vec) + 1e-9)
    m_norm = matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9)
    return m_norm @ q_norm


def _top_k(chunks: list[dict], embeddings: np.ndarray, query_vec: np.ndarray, k: int) -> list[dict]:
    if len(chunks) == 0:
        return []
    sims = _cosine_sim(query_vec, embeddings)
    idx = np.argsort(-sims)[:k]
    return [
        {
            "book": chunks[i]["book"], "title": chunks[i]["title"], "author": chunks[i]["author"],
            "page_start": chunks[i]["page_start"], "page_end": chunks[i]["page_end"],
            "text": chunks[i]["text"], "score": round(float(sims[i]), 4),
        }
        for i in idx
    ]


MAX_K = 6  # hard cap regardless of what the caller requests -- see search_literature() below.


def search_literature(query: str, k: int = 6) -> dict:
    """Returns {"collection_used": "primary"|"hull_fallback", "results": [...]}.
    Each result has book/title/author/page_start/page_end/text/score. Never raises for a normal
    query -- a truly empty corpus or embedding-server-down failure comes back as a result with
    an "error" key instead.

    Both collections are cheap to score (a few thousand chunks total, plain numpy dot products),
    so both are always computed and compared directly rather than gating Hull behind an absolute
    threshold on primary alone -- see FALLBACK_MARGIN for why the absolute-threshold design was
    replaced.

    MISSION literature-research Phase 2: k is hard-capped at MAX_K regardless of what the caller
    requests -- discovered live when the study-loop model asked for k=8 on one of three PARALLEL
    search_literature calls in a single turn, and the combined tool results (17,703 measured
    tokens) blew straight through the model's 16,384-token context in one shot, before
    literature_study.py's own trim logic ever got a chance to run. A single k=6 result already
    measures ~4,256 tokens; capping k here bounds that per-call worst case regardless of how many
    calls happen in parallel."""
    k = max(1, min(int(k), MAX_K))
    try:
        query_vec = embed_query(query)
    except (httpx.HTTPError, httpx.ConnectError) as exc:
        return {"error": f"embedding server unavailable: {exc}"}

    primary_chunks, primary_emb = _load_collection("primary")
    primary_results = _top_k(primary_chunks, primary_emb, query_vec, k)
    best_primary_score = primary_results[0]["score"] if primary_results else 0.0

    hull_chunks, hull_emb = _load_collection("hull")
    hull_results = _top_k(hull_chunks, hull_emb, query_vec, k)
    best_hull_score = hull_results[0]["score"] if hull_results else 0.0

    if best_hull_score - best_primary_score > FALLBACK_MARGIN:
        return {
            "collection_used": "hull_fallback",
            "note": (
                f"Hull scored higher ({best_hull_score:.3f} vs {best_primary_score:.3f} in "
                f"Harris/Cartea) by more than the {FALLBACK_MARGIN} noise margin -- this query "
                f"looks like derivatives/pricing theory rather than market microstructure."
            ),
            "best_score": best_hull_score, "results": hull_results,
        }
    return {
        "collection_used": "primary", "best_score": best_primary_score,
        "results": primary_results,
    }


SEARCH_LITERATURE_TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "search_literature",
        "description": (
            "Search the three-book corpus (Harris' Trading and Exchanges, Cartea/Jaimungal/"
            "Penalva's Algorithmic and High-Frequency Trading, and Hull's Options Futures and "
            "Other Derivatives) for passages relevant to your query. Searches Harris+Cartea "
            "first (the two directly relevant to market microstructure/order flow); Hull is only "
            "searched as a fallback when neither has a good match, since Hull is mostly "
            "derivatives pricing theory, not intraday order-flow prediction. Returns up to k "
            "passages with book, page range, and the actual (OCR-extracted, occasionally "
            "imperfect especially around equations/Greek letters) text."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for."},
                "k": {"type": "integer", "description": "How many passages to return (default 6)."},
            },
            "required": ["query"],
        },
    },
}
