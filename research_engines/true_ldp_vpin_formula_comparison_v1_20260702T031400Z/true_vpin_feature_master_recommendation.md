# True LDP VPIN — Feature Master Recommendation
Generated: 2026-07-02T03:20:49Z
SHADOW / RESEARCH ONLY

## Decision: INCLUDE_BOTH

## Rationale
True VPIN (V500 W50) shows partial divergence from current VPIN (ρ=0.567). Include both to capture independent signal components.

## Evidence Summary
- Best Spearman ρ (true vs current VPIN): 0.9327
  (tvpin_V500_W10)
- Representative setting (V500 W50): ρ = 0.5671
- State agreement rate (V500 W50): 65.4%

## Implementation Complexity
True VPIN requires:
1. Streaming raw trade files for each date (6.1M trades across 14 dates)
2. Maintaining bucket accumulator state (reset per session)
3. Splitting trades at bucket boundaries
4. Aligning bucket VPIN to bar timestamps (searchsorted O(n log n))
5. Additional ~2–5 GB RAM for full history in memory
6. Approximately 60–120 seconds of additional Feature Master compute per day

## FINAL DECISION
- KEEP current bar-level VPIN approximation as primary signal
- True VPIN may be added as a supplementary validation signal in SHADOW mode
- DO NOT replace current VPIN without 90-day OOS walk-forward test

## Safety Constraints
- DO NOT add to Feature Master until pipeline review is complete
- DO NOT enable in production until SHADOW observation period ≥ 30 trading days
- DO NOT use for trade entries under any circumstances (research only)
