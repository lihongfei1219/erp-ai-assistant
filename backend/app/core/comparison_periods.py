"""Calendar-aware comparison periods, independent of model language and business rows."""

import calendar
from datetime import date, timedelta


def growth_periods(step):
    start, end = step.start_date, step.end_date_exclusive
    days = (end - start).days
    if not 1 <= days <= 90:
        raise ValueError("每期须为1至90个完整日")
    last = end - timedelta(days=1)
    full_month = start.day == 1 and last.month == start.month and last.year == start.year
    full_month = full_month and last.day == calendar.monthrange(last.year, last.month)[1]
    if full_month:
        before_end = start
        before_start = (start - timedelta(days=1)).replace(day=1)
    else:
        before_start, before_end = start - timedelta(days=days), start

    def prior_year(value):
        return date(
            value.year - 1,
            value.month,
            min(value.day, calendar.monthrange(value.year - 1, value.month)[1]),
        )

    periods = {"current": (start, end)}
    if step.growth_basis in {"both", "previous"}:
        periods["previous"] = (before_start, before_end)
    if step.growth_basis in {"both", "year_over_year"}:
        periods["year_over_year"] = (prior_year(start), prior_year(last) + timedelta(days=1))
    for key, (begin, until) in periods.items():
        if not 1 <= (until - begin).days <= 90 or key != "current" and until > start:
            raise ValueError("比较期必须有效且与本期不重叠")
    return periods
