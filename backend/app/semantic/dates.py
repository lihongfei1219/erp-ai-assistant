"""Deterministic business dates, anchored to the message rather than a snapshot."""

import re
import unicodedata
from datetime import date, timedelta

_DIGITS = dict(zip("零〇一二三四五六七八九两", [0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 2], strict=True))
_CN = r"[零〇一二三四五六七八九十百两]+"
_DAY = r"(?:(?P<y>\d{4})年)?(?P<m>\d{1,2})月(?P<d>\d{1,2})[日号]?"
_END = r"(?:(?P<ey>\d{4})年)?(?:(?P<em>\d{1,2})月)?(?P<ed>\d{1,2})[日号]?"
_EXPLICIT = re.compile(_DAY + r"(?:(?:至|到|~|～|—)" + _END + r")?")
_RELATIVE = re.compile(
    r"今天|今日|昨天|昨日|前天|上周|本周|这周|本月|这个月|"
    r"(?:最近|过去|近|之前)(?:\d+天|一周|1周|两周|2周)"
)
_CURRENT = re.compile(r"现在|当前|此刻")


def number(value: str) -> int:
    if value.isdigit():
        return int(value)
    if "百" in value:
        left, right = value.split("百", 1)
        return _DIGITS[left] * 100 + (number(right.lstrip("零")) if right.strip("零") else 0)
    if "十" in value:
        left, right = value.split("十", 1)
        return (_DIGITS[left] if left else 1) * 10 + (_DIGITS[right] if right else 0)
    return int("".join(str(_DIGITS[c]) for c in value))


def normalize(value: str) -> str:
    value = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))
    # Convert numeric phrases, not lexical 一 in 一共/一下/一起.
    value = re.sub(_CN + r"(?=[年月日号天周名个件盒瓶]|$)", lambda m: str(number(m[0])), value)
    return re.sub(
        r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", lambda m: f"{m[1]}年{m[2]}月{m[3]}日", value
    )


def resolve_period(expression: str, today: date) -> tuple[date, date]:
    text = normalize(expression)
    if match := _EXPLICIT.fullmatch(text):
        year, month, day = int(match["y"] or today.year), int(match["m"]), int(match["d"])
        start = date(year, month, day)
        end = date(int(match["ey"] or year), int(match["em"] or month), int(match["ed"] or day))
        if end < start:
            raise ValueError("日期先后或跨年范围不明确")
        return start, end + timedelta(days=1)
    if match := re.fullmatch(r"(\d{1,2})[日号]", text):
        start = date(today.year, today.month, int(match[1]))
        return start, start + timedelta(days=1)
    offsets = {
        "今天": 0,
        "today": 0,
        "今日": 0,
        "现在": 0,
        "当前": 0,
        "此刻": 0,
        "昨天": 1,
        "昨日": 1,
        "前天": 2,
    }
    if text in offsets:
        start = today - timedelta(days=offsets[text])
        return start, start + timedelta(days=1)
    if text == "上周":
        end = today - timedelta(days=today.weekday())
        return end - timedelta(days=7), end
    if text in {"本周", "这周", "本月", "这个月"}:
        start = today.replace(day=1) if "月" in text else today - timedelta(days=today.weekday())
        return start, today + timedelta(days=1)
    if match := re.fullmatch(r"(?:最近|过去|近|之前)?(\d+)(天|周)", text):
        days = int(match[1]) * (7 if match[2] == "周" else 1)
        if not 1 <= days <= 90:
            raise ValueError("只支持1至90天")
        return today - timedelta(days=days), today
    raise ValueError("请明确日期范围")


def explicit_periods(
    question: str, today: date, *, include_current=False
) -> list[tuple[date, date]]:
    text = normalize(question)
    periods, occupied = [], []
    patterns = [_EXPLICIT, _RELATIVE, re.compile(r"(?<!\d)\d{1,2}[日号]")]
    if include_current:
        patterns.append(_CURRENT)
    for pattern in patterns:
        for match in pattern.finditer(text):
            if any(match.start() < end and match.end() > start for start, end in occupied):
                continue
            periods.append(resolve_period(match[0], today))
            occupied.append(match.span())
    return periods


def without_dates(question: str) -> str:
    text = normalize(question)
    for pattern in (_EXPLICIT, _RELATIVE, re.compile(r"(?<!\d)\d{1,2}[日号]")):
        text = pattern.sub(" ", text)
    return text


def canonical_period(start: date, end: date) -> str:
    return f"{start.isoformat()}至{(end - timedelta(days=1)).isoformat()}"
