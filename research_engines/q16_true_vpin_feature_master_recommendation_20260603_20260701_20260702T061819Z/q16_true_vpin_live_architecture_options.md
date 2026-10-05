# True VPIN Live Architecture Options
Generated: 2026-07-02T06:31:15Z
SHADOW / RESEARCH ONLY

## Option 1: Feature Master computes true VPIN directly
**Approach**: Feature Master daemon reads raw trades.ndjson at bar seal time,
accumulates bucket state in memory, computes VPIN and aligns to sealed bar.

Pros:
- No extra daemon / IPC overhead
- Single source of truth for bar timestamps
- State stored in Feature Master's in-memory state dict

Cons:
- Feature Master must now read raw trade files (new dependency)
- If raw capture fails, Feature Master fallback path needed
- Adds ~1,105 ms/day of compute

Recommendation: **FEASIBLE** if raw trade file path is stable. Requires
graceful degradation when trades.ndjson is missing.

## Option 2: Separate true_vpin_cache_daemon.py
**Approach**: A lightweight daemon runs alongside Feature Master. It reads
raw trades, builds buckets, and writes a small `true_vpin_latest.json` or
`true_vpin_state.parquet` after each trade ingestion. Feature Master reads
the latest value when sealing each bar.

Pros:
- Clean separation of concerns
- Feature Master retains its current structure
- Daemon can run with minimal latency overhead
- State file persists across daemon restarts

Cons:
- IPC latency (file read at bar seal time)
- File locking risk if daemon writes while Feature Master reads
- One more process to monitor and restart

Recommendation: **PREFERRED** for production. Use atomic file writes (write
to temp then rename) to avoid race conditions.

## Option 3: Research-only (no live compute)
Keep true VPIN as a research artifact. Recompute periodically from
historical raw trade files (e.g., nightly batch). Do not add to Feature
Master real-time pipeline.

Pros:
- Zero production risk
- Full historical recompute on demand

Cons:
- Not available for live decision support

Recommendation: Use Option 3 during the 30-day shadow observation period.
Promote to Option 1 or 2 after shadow confirms value.

## Daemon architecture recommendation (if promoted)
```
true_vpin_cache_daemon.py
  - Watches trades.ndjson for new records (inotify or polling)
  - Maintains bucket accumulator state per session
  - After each bucket completion, writes:
      /tmp/true_vpin_cache/latest.json
        { "ts_ns": ..., "V500_W10": 0.73, "V500_W50": 0.81, ... }
  - Feature Master reads latest.json at bar seal time
  - If file is stale (>60s), Feature Master falls back to current VPIN
```
