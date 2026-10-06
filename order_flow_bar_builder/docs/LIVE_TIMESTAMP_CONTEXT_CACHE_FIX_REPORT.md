# Book Flow V3 Live Timestamp / Context / Cache Fix Report

BACKUP_CREATED: PASS

BACKUP_DIR: `/home/prabh/OFI_Production/backups/book_flow_v3_timestamp_context_cache_fix_20260617T012608Z`

TIMESTAMP_PARITY_FIXED: PASS. Cache timestamp parity is `True` and cache timestamp lag by depth is `{'10': 0.0, '15': 0.0, '20': 0.0, '5': 0.0}`.

LATEST_FEATURE_TIMESTAMP_UTC: `2026-06-17 01:36:51.012590000+00:00`

CACHE_LAST_TIMESTAMP_UTC_BY_DEPTH: `{'10': '2026-06-17 01:36:51.012590+00:00', '15': '2026-06-17 01:36:51.012590+00:00', '20': '2026-06-17 01:36:51.012590+00:00', '5': '2026-06-17 01:36:51.012590+00:00'}`

GUI_RENDERED_MAX_TIMESTAMP_UTC: `2026-06-17 01:32:04.116503+00:00`

TIMESTAMP_LAG_SECONDS_BY_DEPTH: `{'10': 0.0, '15': 0.0, '20': 0.0, '5': 0.0}`

LIVE_LATEST_CONTEXT_ENABLED: PASS

PREVIOUS_SESSION_CONTEXT_SUPPORTED: PASS

CONTEXT_SESSIONS_LOADED: `['2026-06-15', '2026-06-16']`

CACHE_MIN_BAR_IDX_BY_DEPTH: `{'5': 1623, '10': 1623, '15': 1623, '20': 1623}`

CACHE_MAX_BAR_IDX_BY_DEPTH: `{'5': 1667, '10': 1667, '15': 1667, '20': 1667}`

UNIQUE_CACHED_BARS_BY_DEPTH: `{'5': 23, '10': 28, '15': 31, '20': 35}`

MISSING_BAR_COUNT_BY_DEPTH: `{'5': 22, '10': 17, '15': 14, '20': 10}`

LAST_20_BARS_CELL_COUNT_BY_DEPTH: `{'5': {'1648': 0, '1649': 2, '1650': 0, '1651': 0, '1652': 0, '1653': 12, '1654': 0, '1655': 0, '1656': 10, '1657': 6, '1658': 0, '1659': 0, '1660': 0, '1661': 18, '1662': 3, '1663': 0, '1664': 5, '1665': 7, '1666': 9, '1667': 5}, '10': {'1648': 0, '1649': 2, '1650': 0, '1651': 1, '1652': 0, '1653': 16, '1654': 0, '1655': 0, '1656': 11, '1657': 6, '1658': 0, '1659': 0, '1660': 13, '1661': 23, '1662': 4, '1663': 0, '1664': 6, '1665': 7, '1666': 10, '1667': 5}, '15': {'1648': 0, '1649': 2, '1650': 0, '1651': 1, '1652': 0, '1653': 17, '1654': 0, '1655': 0, '1656': 12, '1657': 6, '1658': 0, '1659': 0, '1660': 16, '1661': 23, '1662': 4, '1663': 0, '1664': 6, '1665': 9, '1666': 13, '1667': 5}, '20': {'1648': 9, '1649': 2, '1650': 0, '1651': 1, '1652': 0, '1653': 18, '1654': 0, '1655': 0, '1656': 12, '1657': 6, '1658': 1, '1659': 0, '1660': 16, '1661': 24, '1662': 4, '1663': 0, '1664': 6, '1665': 9, '1666': 13, '1667': 5}}`

PARTIAL_BARS_DIAGNOSED: PASS. Latest feature/cache bar `1667` is present in cache parity, but the GUI default hides it because it is marked FORMING/PARTIAL. GUI rendered max is bar `1666` at `2026-06-17 01:32:04.116503+00:00`. The `286.896087` second GUI lag is explicitly explained as: `latest feature/cache bar is marked FORMING/PARTIAL and hidden by default`.

FORMING_BAR_EXCLUDED_BY_DEFAULT: PASS. Hidden forming bars: `[1622, 1667]`. `show_forming_bar` default: `False`.

LATEST_CLOSED_BAR_RENDERED: PASS. Cache-confirmed closed bar `1666` is rendered; latest feature bar `1667` is not shown as completed.

CACHE_CARRIES_ENOUGH_HISTORY: PASS. Loaded bars `1002`, requested lookback `1000`, shortage `0`.

GUI_LOADS_SELECTED_LOOKBACK: PASS. GUI loaded `1002` bars and rendered `1000` bars for lookback `1000` with max visible cap `1200`.

CONTEXT_DOES_NOT_BREAK_LIVE_UPDATES: PASS. Daemon one-shot elapsed `57.115` ms with status `PASS`; GUI context dates loaded `['2026-06-15', '2026-06-16']`.

CHART_SEMANTICS_CHANGED: NO. Price-axis book-flow level-cell semantics, top 5/10/15/20 depth logic, and visual design were not changed. The fix changes loading/windowing/diagnostics only.

TRADING_ENABLED: NO

VALIDATION:

- `py_compile book_flow_chart_v3.py`: PASS
- `py_compile book_flow_cache_daemon.py`: PASS
- `py_compile build_book_flow_level_cache.py`: PASS
- `bash -n launch_book_flow_chart.sh`: PASS
- `book_flow_cache_daemon.py --symbol NQU6 --date latest --once`: PASS
- `book_flow_chart_v3.py --smoke-test --date latest`: PASS

OVERALL PASS
