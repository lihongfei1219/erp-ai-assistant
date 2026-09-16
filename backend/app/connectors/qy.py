"""Fixed SELECT queries against the restored QY schema. Never accepts user SQL."""

from dataclasses import dataclass
from decimal import Decimal
from time import perf_counter

import pandas as pd
from sqlalchemy import URL, bindparam, create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from app.schemas.sales import AnalysisWindow, DataScope


class SourceDataError(ValueError):
    pass


@dataclass
class SalesExtract:
    orders: pd.DataFrame
    lines: pd.DataFrame
    sql_order_count: int
    sql_order_amount: Decimal
    read_seconds: float


def make_source_engine(odbc_connection: str) -> Engine:
    engine = create_engine(
        URL.create("mssql+pyodbc", query={"odbc_connect": odbc_connection}),
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={"timeout": 10},
        # One bounded transaction for both tables and control totals. No NOLOCK.
        isolation_level="SERIALIZABLE",
    )

    @event.listens_for(engine, "connect")
    def configure_timeout(connection, _record):
        connection.timeout = 30

    return engine


def _query(sql: str, scope: DataScope):
    statement = text(sql)
    if not scope.all_buyers:
        statement = statement.bindparams(bindparam("buyer_codes", expanding=True))
    return statement


def read_sales(
    engine: Engine,
    window: AnalysisWindow,
    scope: DataScope,
    *,
    max_orders: int = 50_000,
    max_lines: int = 200_000,
) -> SalesExtract:
    if max_orders <= 0 or max_lines <= 0:
        raise ValueError("读取上限必须为正数")
    predicate = "h.CJRQ >= :start AND h.CJRQ < :end"
    params = {"start": window.start, "end": window.end}
    if not scope.all_buyers:
        predicate += " AND h.DWBM IN :buyer_codes"
        params["buyer_codes"] = list(scope.buyer_codes)
    # Only fixed identifiers and validated integer limits are interpolated.
    orders_sql = f"""
        SELECT TOP ({int(max_orders) + 1}) h.DjLsh AS order_id,
               RTRIM(h.BJDH) AS order_number, RTRIM(h.DWBM) AS buyer_code,
               RTRIM(h.DWMC) AS buyer_name,
               h.CJRQ AS created_at, RTRIM(h.DDZT) AS status, h.ZJE AS amount
        FROM dbo.XSDDH h WHERE {predicate} ORDER BY h.DjLsh
    """
    lines_sql = f"""
        SELECT TOP ({int(max_lines) + 1}) b.DjLsh AS order_id, b.DjBth AS line_id,
               RTRIM(b.SPBM) AS product_code, b.SL AS quantity,
               RTRIM(b.SPMC) AS product_name,
               b.DJ AS unit_price, b.JE AS amount
        FROM dbo.XSDDB b JOIN dbo.XSDDH h ON h.DjLsh=b.DjLsh
        WHERE {predicate} ORDER BY b.DjLsh, b.DjBth
    """
    control_sql = f"""
        SELECT COUNT_BIG(*) AS order_count, COALESCE(SUM(h.ZJE), 0) AS order_amount
        FROM dbo.XSDDH h WHERE {predicate}
    """
    started = perf_counter()
    with engine.connect() as connection, connection.begin():
        orders = pd.read_sql_query(
            _query(orders_sql, scope), connection, params=params, coerce_float=False
        )
        if len(orders) > max_orders:
            raise SourceDataError("订单数超过上限，请缩小日期或企业范围")
        lines = pd.read_sql_query(
            _query(lines_sql, scope), connection, params=params, coerce_float=False
        )
        if len(lines) > max_lines:
            raise SourceDataError("明细数超过上限，请缩小日期或企业范围")
        control = connection.execute(_query(control_sql, scope), params).mappings().one()
    return SalesExtract(
        orders=orders,
        lines=lines,
        sql_order_count=int(control["order_count"]),
        sql_order_amount=control["order_amount"],
        read_seconds=perf_counter() - started,
    )
