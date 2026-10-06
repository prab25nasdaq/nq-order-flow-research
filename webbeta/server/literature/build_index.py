#!/usr/bin/env python3
"""
MISSION literature-research Phase 1: builds the literature search index.

Extracts per-page text from each book's PDF (pdftotext -layout, already-verified text layers --
see LITERATURE_RESEARCH_REPORT.md Phase 0), chunks with page tracking (~700 tokens, ~15% overlap,
snapped to paragraph breaks where possible per Larry -- Harris builds arguments over pages, so
favour fewer larger chunks over many small ones), embeds each chunk locally via a dedicated
llama-server instance running nomic-embed-text-v1.5 in --embedding mode (port 8091, started
separately -- see the report for why this model/approach), and writes TWO SEPARATE collections
to disk: "primary" (Harris + Cartea/Jaimungal/Penalva merged) and "hull" (alone) -- per direct
instruction: querying merges Harris+Cartea only, Hull is queried as a fallback, never merged in,
since at 685k tokens it's roughly half the corpus and the least relevant; merging would dilute
every microstructure query.

Run once to build the index; server/literature_search.py loads the resulting files at query time.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import httpx
import numpy as np

OUT_DIR = Path(__file__).resolve().parent
EMBED_URL = "http://127.0.0.1:8091/v1/embeddings"
EMBED_BATCH_SIZE = 32

TARGET_TOKENS = 700
OVERLAP_FRACTION = 0.15
PARAGRAPH_SNAP_WINDOW = 150  # chars forward to look for a clean paragraph break

# Chars-per-token measured LIVE via the main model's /tokenize endpoint on the full extracted
# text of each book (see LITERATURE_RESEARCH_REPORT.md Phase 0) -- not guessed, not a generic
# ratio. Different books tokenize at meaningfully different densities.
BOOKS = {
    "harris": {
        "path": "/home/prabh/Documents/Books/Trading and Exchanges: Market Microstructure for Practitioners by Larry Harris.PDF",
        "title": "Trading and Exchanges: Market Microstructure for Practitioners",
        "author": "Larry Harris",
        "collection": "primary",
        "chars_per_token": 5.21,
    },
    "cartea": {
        "path": "/home/prabh/Documents/Books/Algorithmic and High-Frequency Trading.pdf",
        "title": "Algorithmic and High-Frequency Trading",
        "author": "Cartea, Jaimungal, Penalva",
        "collection": "primary",
        "chars_per_token": 4.24,
    },
    "hull": {
        "path": "/home/prabh/Documents/Books/Options, Futures and Other Derivatives 8th John Hull.pdf",
        "title": "Options, Futures and Other Derivatives",
        "author": "John Hull",
        "collection": "hull",
        "chars_per_token": 4.38,
    },
}


def extract_pages(pdf_path: str) -> list[str]:
    result = subprocess.run(["pdftotext", "-layout", pdf_path, "-"],
                             capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise RuntimeError(f"pdftotext failed for {pdf_path}: {result.stderr[:500]}")
    pages = result.stdout.split("\x0c")
    if pages and pages[-1].strip() == "":
        pages = pages[:-1]
    return pages


def build_offset_map(pages: list[str]) -> tuple[str, list[tuple[int, int]]]:
    full_text = ""
    offsets = []
    for p in pages:
        start = len(full_text)
        full_text += p
        end = len(full_text)
        offsets.append((start, end))
        full_text += "\n"
    return full_text, offsets


def page_for_offset(offsets: list[tuple[int, int]], char_offset: int) -> int:
    for i, (start, end) in enumerate(offsets):
        if start <= char_offset <= end:
            return i + 1  # 1-indexed page number
    return len(offsets)


def chunk_book(full_text: str, offsets: list[tuple[int, int]], chars_per_token: float) -> list[dict]:
    target_chars = int(TARGET_TOKENS * chars_per_token)
    step_chars = int(target_chars * (1 - OVERLAP_FRACTION))
    chunks = []
    pos = 0
    n = len(full_text)
    while pos < n:
        end = min(pos + target_chars, n)
        if end < n:
            window = full_text[end:end + PARAGRAPH_SNAP_WINDOW]
            m = re.search(r"\n\s*\n", window)
            if m:
                end = end + m.start()
        text = full_text[pos:end].strip()
        if text:
            page_start = page_for_offset(offsets, pos)
            page_end = page_for_offset(offsets, max(end - 1, pos))
            chunks.append({
                "text": text,
                "page_start": page_start,
                "page_end": page_end,
            })
        if end >= n:
            break
        pos += step_chars
    return chunks


def embed_batch(client: httpx.Client, texts: list[str]) -> list[list[float]]:
    resp = client.post(EMBED_URL, json={"input": texts}, timeout=120.0)
    resp.raise_for_status()
    data = resp.json()["data"]
    return [d["embedding"] for d in data]


def main() -> None:
    client = httpx.Client()
    for key, meta in BOOKS.items():
        print(f"=== {key} ({meta['title']}) ===")
        pages = extract_pages(meta["path"])
        print(f"  {len(pages)} pages extracted")
        full_text, offsets = build_offset_map(pages)
        chunks = chunk_book(full_text, offsets, meta["chars_per_token"])
        print(f"  {len(chunks)} chunks (~{TARGET_TOKENS} tok target, {OVERLAP_FRACTION:.0%} overlap)")

        embeddings = []
        for i in range(0, len(chunks), EMBED_BATCH_SIZE):
            batch = chunks[i:i + EMBED_BATCH_SIZE]
            vecs = embed_batch(client, [c["text"] for c in batch])
            embeddings.extend(vecs)
            print(f"  embedded {min(i + EMBED_BATCH_SIZE, len(chunks))}/{len(chunks)}", end="\r")
        print()

        for c, book_key in zip(chunks, [key] * len(chunks)):
            c["book"] = book_key
            c["title"] = meta["title"]
            c["author"] = meta["author"]

        book_out = OUT_DIR / f"chunks_{key}.json"
        book_out.write_text(json.dumps(chunks, indent=None))
        emb_out = OUT_DIR / f"embeddings_{key}.npy"
        np.save(emb_out, np.array(embeddings, dtype=np.float32))
        print(f"  wrote {book_out.name} and {emb_out.name}")

    # Assemble the two collections the design calls for.
    for collection in ("primary", "hull"):
        book_keys = [k for k, m in BOOKS.items() if m["collection"] == collection]
        all_chunks = []
        all_embeddings = []
        for key in book_keys:
            chunks = json.loads((OUT_DIR / f"chunks_{key}.json").read_text())
            emb = np.load(OUT_DIR / f"embeddings_{key}.npy")
            all_chunks.extend(chunks)
            all_embeddings.append(emb)
        combined_emb = np.concatenate(all_embeddings, axis=0) if all_embeddings else np.zeros((0, 768), dtype=np.float32)
        (OUT_DIR / f"collection_{collection}.json").write_text(json.dumps(all_chunks, indent=None))
        np.save(OUT_DIR / f"collection_{collection}.npy", combined_emb)
        print(f"collection '{collection}': {len(all_chunks)} chunks from {book_keys}")

    client.close()


if __name__ == "__main__":
    main()
