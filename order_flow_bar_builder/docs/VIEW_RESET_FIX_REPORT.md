# VIEW_RESET_FIX_REPORT

Generated: 2026-06-15 UTC

## Scope

Patched only:

- `/home/prabh/OFI_Production/book_flow_chart/book_flow_chart_v3.py`

No book-flow candle logic, raw book reconstruction, dashboard code, parser/scheduler, model artifacts, master files, or trading flags were changed.

## Root Cause

V3's refresh path still called `_update_view_ranges()` while Follow Live was checked, even after manual pan/zoom. That function runs `setXRange()` and `setYRange()`, so each data refresh snapped the chart back to the live/latest range.

## Fix

- Added `user_view_override` as the manual pan/zoom lock.
- Added `_programmatic_view_update` guard so programmatic range updates do not mark the view as manually changed.
- `_reload()` now skips `_update_view_ranges()` whenever `user_view_override=True`.
- `_update_view_ranges()` also returns immediately if `user_view_override=True`, preventing refresh-time `setXRange()` / `setYRange()`.
- Manual pan/zoom sets `user_view_override=True` without changing the Follow Live checkbox.
- Reset View is the only code path that clears `user_view_override` and returns to the live/latest range.
- Status label now shows `FOLLOW LIVE: ON/OFF | VIEW: FOLLOWING LIVE / USER LOCKED / MANUAL_VIEW`.

## Validation

Commands run:

- `python -m py_compile /home/prabh/OFI_Production/book_flow_chart/book_flow_chart_v3.py`
- `timeout 15s /home/prabh/.venvs/ofi/bin/python /home/prabh/OFI_Production/book_flow_chart/book_flow_chart_v3.py --smoke-test`

Smoke output confirmed:

- `PAN_ZOOM_PRESERVES_VIEW=true`
- `CURSOR_MOVE_PRESERVES_VIEW=true`
- `DATA_REFRESH_PRESERVES_VIEW=true`
- `RESET_VIEW_STILL_WORKS=true`
- `FOLLOW_LIVE_CHECKBOX_STAYS_CHECKED=true`
- `SMOKE_TEST=PASS`

## Required Final Output

APP_PATH: `/home/prabh/OFI_Production/book_flow_chart/book_flow_chart_v3.py`

VIEW_RESET_ROOT_CAUSE: refresh called `_update_view_ranges()` after manual pan/zoom while Follow Live remained checked, causing `setXRange()` / `setYRange()` to snap back to live.

AUTO_RANGE_ON_REFRESH_DISABLED: true

SET_RANGE_ON_REFRESH_GUARDED: true

USER_VIEW_OVERRIDE_IMPLEMENTED: true

PROGRAMMATIC_RANGE_GUARD_IMPLEMENTED: true

PAN_ZOOM_PRESERVES_VIEW: true

CURSOR_MOVE_PRESERVES_VIEW: true

DATA_REFRESH_PRESERVES_VIEW: true

RESET_VIEW_STILL_WORKS: true

FOLLOW_LIVE_CHECKBOX_STAYS_CHECKED: true

PY_COMPILE: PASS

SMOKE_TEST: PASS

TRADING_ENABLED: false

OVERALL PASS
