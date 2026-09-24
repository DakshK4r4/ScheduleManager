from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Set, Union


@dataclass
class CalendarSpec:
    calendar_id: str = "STANDARD"
    name: str = "Standard 5-Day Workweek"
    working_days: Set[int] = field(default_factory=lambda: {0, 1, 2, 3, 4})  # 0=Monday .. 6=Sunday
    hours_per_day: float = 8.0
    holidays: Set[date] = field(default_factory=set)

    @classmethod
    def standard_5day(cls, calendar_id: str = "5DAY", holidays: Optional[Set[date]] = None) -> CalendarSpec:
        return cls(
            calendar_id=calendar_id,
            name="Standard 5-Day (Mon-Fri)",
            working_days={0, 1, 2, 3, 4},
            hours_per_day=8.0,
            holidays=holidays or set(),
        )

    @classmethod
    def standard_6day(cls, calendar_id: str = "6DAY", holidays: Optional[Set[date]] = None) -> CalendarSpec:
        return cls(
            calendar_id=calendar_id,
            name="Standard 6-Day (Mon-Sat)",
            working_days={0, 1, 2, 3, 4, 5},
            hours_per_day=8.0,
            holidays=holidays or set(),
        )

    @classmethod
    def continuous_7day(cls, calendar_id: str = "7DAY", holidays: Optional[Set[date]] = None) -> CalendarSpec:
        return cls(
            calendar_id=calendar_id,
            name="Continuous 7-Day",
            working_days={0, 1, 2, 3, 4, 5, 6},
            hours_per_day=8.0,
            holidays=holidays or set(),
        )


class CalendarService:
    DEFAULT_CALENDAR = CalendarSpec.standard_5day()

    @staticmethod
    def _to_date(val: Union[date, datetime, str]) -> date:
        if isinstance(val, datetime):
            return val.date()
        if isinstance(val, date):
            return val
        if isinstance(val, str):
            # Parse ISO date string
            clean = val.replace("Z", "+00:00").split("T")[0]
            return datetime.strptime(clean, "%Y-%m-%d").date()
        raise ValueError(f"Cannot convert {type(val)} to date")

    @classmethod
    def is_working_day(cls, d: Union[date, datetime, str], cal: Optional[CalendarSpec] = None) -> bool:
        c = cal or cls.DEFAULT_CALENDAR
        dt = cls._to_date(d)
        if dt.weekday() not in c.working_days:
            return False
        if dt in c.holidays:
            return False
        return True

    @classmethod
    def next_working_day(cls, d: Union[date, datetime, str], cal: Optional[CalendarSpec] = None) -> date:
        dt = cls._to_date(d)
        while not cls.is_working_day(dt, cal):
            dt += timedelta(days=1)
        return dt

    @classmethod
    def prev_working_day(cls, d: Union[date, datetime, str], cal: Optional[CalendarSpec] = None) -> date:
        dt = cls._to_date(d)
        while not cls.is_working_day(dt, cal):
            dt -= timedelta(days=1)
        return dt

    @classmethod
    def add_working_days(
        cls,
        start_date: Union[date, datetime, str],
        duration_days: float,
        cal: Optional[CalendarSpec] = None,
    ) -> date:
        """
        Adds working days to start_date.
        In CPM conventions:
        - If an activity starts on Monday with duration 1 day, it finishes on Monday.
        - If duration is N days (N >= 1), finish is reached after traversing N-1 working days from the start.
        - If duration is 0 (milestone), finish = start.
        - Fractional durations are treated with ceiling whole working days for discrete calendar dates.
        """
        c = cal or cls.DEFAULT_CALENDAR
        cur = cls.next_working_day(start_date, c)

        if duration_days <= 0:
            return cur

        days_needed = int(round(duration_days))
        # If duration is 1 day, it finishes on 'cur'
        remaining = days_needed - 1
        while remaining > 0:
            cur += timedelta(days=1)
            if cls.is_working_day(cur, c):
                remaining -= 1

        return cur

    @classmethod
    def subtract_working_days(
        cls,
        finish_date: Union[date, datetime, str],
        duration_days: float,
        cal: Optional[CalendarSpec] = None,
    ) -> date:
        """
        Subtracts working days from finish_date to find start_date.
        If finish is Friday and duration is 1 day, start is Friday.
        If duration is N days, start is reached after stepping backward N-1 working days.
        """
        c = cal or cls.DEFAULT_CALENDAR
        cur = cls.prev_working_day(finish_date, c)

        if duration_days <= 0:
            return cur

        days_needed = int(round(duration_days))
        remaining = days_needed - 1
        while remaining > 0:
            cur -= timedelta(days=1)
            if cls.is_working_day(cur, c):
                remaining -= 1

        return cur

    @classmethod
    def working_days_between(
        cls,
        start_date: Union[date, datetime, str],
        finish_date: Union[date, datetime, str],
        cal: Optional[CalendarSpec] = None,
    ) -> float:
        """
        Calculates the number of working days between start_date and finish_date inclusive.
        If finish < start, returns negative working days.
        """
        c = cal or cls.DEFAULT_CALENDAR
        d_start = cls._to_date(start_date)
        d_finish = cls._to_date(finish_date)

        if d_finish < d_start:
            return -cls.working_days_between(d_finish, d_start, cal)

        count = 0
        cur = d_start
        while cur <= d_finish:
            if cls.is_working_day(cur, c):
                count += 1
            cur += timedelta(days=1)

        return float(count)
