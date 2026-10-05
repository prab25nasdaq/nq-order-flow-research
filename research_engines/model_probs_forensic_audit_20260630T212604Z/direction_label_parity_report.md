# Class/Direction Mapping Parity Report

## Verdict: PASS — No class mapping error found.

- class_0 = SHORT (label_h40=0, future_close <= event_close)
- class_1 = LONG  (label_h40=1, future_close > event_close)
- pred_class = (p_long >= p_short).astype(int) — consistent training/inference
- hit = (pred_class == label_h40) — correct comparison
- The `direction` column in predictions applies confidence threshold:
  - LONG  if p_long  >= 0.65
  - SHORT if p_short >= 0.65
  - FLAT  otherwise (confidence < threshold, even if pred_class=1)

## Class reversal check: CLEAR
p_long and p_short are not reversed. Verified via parity replay in PARITY_REPORT.md (G.3 pass=True).

## Note on last_25/100 0.0% hit rate:
This is NOT caused by class reversal. It is genuine model failure on specific bars (June 29 bars 888-899).
