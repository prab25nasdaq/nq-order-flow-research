# Raw Rithmic File Date Mismatch Report
Generated: 2026-07-02T06:31:15Z
Target range: 2026-06-03T00:00:00+00:00 → 2026-07-01T23:59:59+00:00

## Files audited: 21
## Files included (have in-range records): 21
## Total in-range trade records: 11,604,407

## Files with out-of-range records (date spillover):
  2026-07-01/NQU6: before=0  after=45226  span=2026-07-01T22:00:03.139387+00:00 → 2026-07-02T06:31:52.533479+00:00

## Symbol roll observation:
  Jun 3–11: raw data under NQM6/ (front month before roll)
  Jun 14+:  raw data under NQU6/ (after quarterly roll)
  True VPIN computed from NQM6 and NQU6 trades — volume clock continuous across roll.