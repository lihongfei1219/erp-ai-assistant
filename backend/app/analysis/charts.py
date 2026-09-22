"""Plotly figures built exclusively from calculated rows, never model text."""

import html
import json

import plotly.graph_objects as go

from app.schemas.analytics import AnalysisResult, AnalysisStep


def build_chart(result: AnalysisResult, step: AnalysisStep, currency: str) -> dict | None:
    if (
        not result.rows
        or step.kind in {"summary", "list", "existence"}
        or step.metric in {"quantity", "stock"}
    ):
        return None
    y_key = (
        "delta"
        if step.kind == "comparison"
        else (
            ("order_count" if step.domain == "sales" else "document_count")
            if step.metric == "orders"
            else "amount"
        )
    )
    labels = [
        html.escape(str(row.get("day") or row.get("name") or row.get("code")))
        for row in result.rows
    ]
    categories = [str(row.get("day") or row["code"]) for row in result.rows]
    # Decimal strings remain in table/download; floats are only drawing coordinates.
    values = [float(row[y_key]) for row in result.rows]
    exact = [str(row[y_key]) for row in result.rows]
    trace_type = go.Scatter if step.kind in {"trend", "anomalies"} else go.Bar
    options = {"mode": "lines+markers"} if trace_type is go.Scatter else {}
    figure = go.Figure(
        trace_type(
            x=categories,
            y=values,
            customdata=exact,
            hovertemplate="%{x}<br>%{customdata}<extra></extra>",
            **options,
        )
    )
    figure.update_layout(
        template="plotly_white",
        height=330,
        margin=dict(l=65, r=20, t=20, b=80),
        yaxis_title=("订单数" if step.domain == "sales" else "单据数")
        if step.metric == "orders"
        else currency,
        xaxis=dict(
            type="category", automargin=True, tickmode="array", tickvals=categories, ticktext=labels
        ),
        showlegend=False,
    )
    return json.loads(figure.to_json())
