"""Feishu JSON 2.0 primitives and a bounded, deterministic display policy."""

from decimal import Decimal
from math import isfinite

from app.analysis.sales_query import _label
from app.integrations.feishu_cards import _money

MAX_CARD_BYTES = 28000
MAX_ELEMENTS = 200
MAX_TABLES = 5
MAX_CHARTS = 2
PAGE_SIZE = 5
ROW_LIMITS = (90, 50, 20, 10, 5, 3, 1)


def text(content: str, *, muted: bool = False, size: str = "normal") -> dict:
    return {
        "tag": "div",
        "text": {
            "tag": "plain_text",
            "content": content,
            "text_size": size,
            "text_color": "grey" if muted else "default",
        },
    }


def amount(value, currency: str) -> str:
    if value is None:
        return "—"
    return ("¥ " if currency == "CNY" else f"{_label(currency, 8)} ") + _money(value)


def percent(value) -> str:
    return f"{Decimal(value) * 100:+.2f}%" if value is not None else "—（比较期为零）"


def indicators(pairs: list[tuple[str, str]]) -> list[dict]:
    # Two columns at most; very large numbers get a full-width row on mobile.
    if any(len(value) > 19 for _, value in pairs):
        return [text(f"{label}\n{value}") for label, value in pairs]
    return [
        {
            "tag": "column_set",
            "flex_mode": "none",
            "columns": [
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "elements": [text(label, muted=True), text(value, size="heading")],
                }
                for label, value in pairs
            ],
        }
    ]


def panel(title: str, content: str) -> dict:
    return {
        "tag": "collapsible_panel",
        "expanded": False,
        "header": {"title": {"tag": "plain_text", "content": title}},
        "elements": [text(content, muted=True)],
    }


def table(columns: list[tuple[str, str]], rows: list[dict]) -> dict:
    return {
        "tag": "table",
        "page_size": PAGE_SIZE,
        "row_height": "middle",
        "freeze_first_column": True,
        "header_style": {"bold": True, "background_style": "grey", "lines": 1},
        "columns": [
            {
                "name": key,
                "display_name": label,
                "data_type": "text",
                "width": "auto",
                "horizontal_align": "left" if index == 0 else "right",
            }
            for index, (key, label) in enumerate(columns)
        ],
        "rows": rows,
    }


def chart(rows: list[dict], *, kind: str, field: str, title: str) -> dict | None:
    values = []
    for index, row in enumerate(rows, 1):
        # JSON numbers are only plotting coordinates; money in tables uses Decimal.
        value = row.get(field)
        if value is None:
            return None  # never turn unknown data into a zero
        number = float(value)
        if not isfinite(number) or abs(number) > 2**53 - 1:
            return None  # unsafe coordinate range: retain exact table only
        label = (
            str(row["day"])
            if kind == "line"
            else f"{index}. {_label(str(row.get('name') or row.get('code', '')), 14)}"
        )
        values.append({"label": label, "value": number})
    if len(values) < 2:
        return None
    spec = {
        "type": kind,
        "title": {"text": title},
        "data": {"values": values},
        "xField": "label" if kind == "line" else "value",
        "yField": "value" if kind == "line" else "label",
    }
    if kind == "bar":
        spec["direction"] = "horizontal"
    return {"tag": "chart", "chart_spec": spec, "height": "240px", "preview": True}


def element_count(value) -> int:
    if isinstance(value, dict):
        return int("tag" in value) + sum(element_count(item) for item in value.values())
    if isinstance(value, list):
        return sum(element_count(item) for item in value)
    return 0
