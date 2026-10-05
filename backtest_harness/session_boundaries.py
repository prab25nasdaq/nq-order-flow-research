"""
CME NQ trading-session boundaries -- MISSION lookback-sessions-and-timestamps.

Mirrors the exact rules already established in /home/prabh/OFI_Production/master_live/cme_schedule.py
(the training pipeline's own convention) rather than inventing a second, possibly-inconsistent
definition: daily maintenance break 21:00-22:00 UTC Mon-Thu, weekly close Fri 21:00 UTC -> Sun
22:00 UTC. Not imported directly -- this codebase's own established pattern (confirmed directly,
e.g. rithmic_scheduler.py reimplementing cme_schedule.py's own rules rather than importing it) is
each service owns its copy of these constants rather than a cross-project import. Cited explicitly
here so the two can't silently drift without a reviewer noticing.

A session starts at 22:00 UTC and, in the normal case, ends ~23h later at 21:00 UTC the next day
(the daily maintenance break). The one exception: a session that starts Thursday 22:00 UTC ends at
Friday 21:00 UTC (the weekly close) and is not followed by a new session until Sunday 22:00 UTC --
so valid session-start weekdays are Sun/Mon/Tue/Wed/Thu only, never Fri/Sat. This matches the real
observed raw-data folder rollover boundaries (confirmed live during an earlier mission: last write
2026-08-13 22:00:00 UTC [Thursday], first write after the weekend 2026-08-16 22:26 UTC [Sunday]).

The same rule is mirrored client-side in app.js for the continuous "following live" trim, citing
back to this module. Deliberately duplicated rather than round-tripped per tick: the rule is simple
and static (CME's own maintenance schedule changes essentially never), and the INITIAL bar set for
any lookback selection always comes authoritatively from this module server-side regardless -- a
client-side computation error here could only ever affect ongoing local trimming during a live
session, never the initial data-correctness gate.
"""
from __future__ import annotations

import datetime
from typing import Optional

UTC = datetime.timezone.utc

DAILY_BREAK_START_UTC_HOUR = 21   # Mon-Thu
SESSION_START_UTC_HOUR = 22       # Sun-Thu


def current_or_most_recently_started_session_start(now: Optional[datetime.datetime] = None) -> datetime.datetime:
    """The start (22:00 UTC on a Sun/Mon/Tue/Wed/Thu) of whichever session `now` falls inside, or
    -- if `now` falls inside a daily break or the weekend close -- the start of the session that
    most recently began."""
    n = (now or datetime.datetime.now(UTC)).astimezone(UTC)
    base_date = n.date() if n.hour >= SESSION_START_UTC_HOUR else (n - datetime.timedelta(days=1)).date()
    candidate = datetime.datetime(base_date.year, base_date.month, base_date.day,
                                   SESSION_START_UTC_HOUR, 0, 0, tzinfo=UTC)
    while candidate.weekday() in (4, 5):  # Fri, Sat -- no session ever starts on these days
        candidate -= datetime.timedelta(days=1)
    return candidate


def _session_end(start: datetime.datetime) -> datetime.datetime:
    """End of the session beginning at `start` -- always the next day's 21:00 UTC daily break (this
    also correctly covers the Thursday-start session, whose "next day" IS the Friday weekly close)."""
    return (start + datetime.timedelta(days=1)).replace(hour=DAILY_BREAK_START_UTC_HOUR, minute=0,
                                                          second=0, microsecond=0)


def _prior_session_start(start: datetime.datetime) -> datetime.datetime:
    """Start of the session immediately preceding the one beginning at `start`."""
    if start.weekday() == 6:  # Sunday reopen -- the prior session started the preceding Thursday
        return start - datetime.timedelta(days=3)
    return start - datetime.timedelta(days=1)


def last_n_session_boundaries(n: int, now: Optional[datetime.datetime] = None) -> list[dict]:
    """Boundaries of the last `n` sessions, oldest first, as
    [{"start_ts_ns": int, "end_ts_ns": int}, ...]. The LAST entry is whichever session is currently
    running (or, if `now` is inside a break/weekend, the one that most recently started)."""
    if n < 1:
        raise ValueError("n must be >= 1")
    starts = []
    s = current_or_most_recently_started_session_start(now)
    for _ in range(n):
        starts.append(s)
        s = _prior_session_start(s)
    starts.reverse()
    return [
        {"start_ts_ns": int(s.timestamp() * 1_000_000_000), "end_ts_ns": int(_session_end(s).timestamp() * 1_000_000_000)}
        for s in starts
    ]
