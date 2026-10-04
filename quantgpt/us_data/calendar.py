"""Versioned New York regular-session calendar, including holidays and DST."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from importlib.metadata import version
from typing import Any, cast

import exchange_calendars
import pandas as pd


@dataclass(frozen=True)
class TradingSession:
    session: date
    opens_at: datetime
    closes_at: datetime


class NewYorkCalendar:
    name = "XNYS"
    timezone = "America/New_York"

    def __init__(self, start: str = "1990-01-01", end: str = "2035-12-31"):
        self._calendar: Any = exchange_calendars.get_calendar(self.name, start=start, end=end)
        self.version = f"exchange-calendars/{version('exchange-calendars')}:XNYS"

    def sessions(self, start: date, end: date) -> tuple[TradingSession, ...]:
        if start > end:
            raise ValueError("start must not follow end")
        dates = self._calendar.sessions_in_range(start.isoformat(), end.isoformat())
        return tuple(TradingSession(
            session=cast(date, pd.Timestamp(day).date()),
            opens_at=self._calendar.session_open(day).to_pydatetime(),
            closes_at=self._calendar.session_close(day).to_pydatetime(),
        ) for day in dates)
