"""Regression checks for scoped business-source completeness."""

import re

import pytest
from sqlalchemy import create_engine, text

from app.connectors.operations import _read_documents
from app.connectors.qy import SourceDataError, _query
from app.schemas.sales import DataScope


class SqliteDialect:
    def __init__(self, connection, scope):
        self.connection = connection
        self.scope = scope

    def execute(self, statement, params):
        sql = str(statement).replace("COUNT_BIG(", "COUNT(").replace("N'", "'")
        sql = re.sub(r"__\[POSTCOMPILE_buyer_codes\]", ":buyer_codes", sql)
        match = re.search(r"TOP \((\d+)\)", sql)
        if match:
            sql = sql.replace(match[0], "") + " LIMIT " + match[1]
        translated = _query(sql, self.scope) if ":buyer_codes" in sql else text(sql)
        return self.connection.execute(translated, params)


@pytest.mark.parametrize("original_buyer", [None, "BUYER-B"])
def test_authorized_return_cannot_disappear_when_original_order_is_invalid(report, original_buyer):
    """Execute the real bounded SELECTs on synthetic SQL rows (translate MSSQL syntax only)."""
    engine = create_engine("sqlite://")
    scope = DataScope(buyer_codes=["BUYER-A"])
    report = report.model_copy(
        update={"metadata": report.metadata.model_copy(update={"scope": scope})}
    )
    with engine.begin() as connection:
        connection.execute(text("ATTACH DATABASE ':memory:' AS dbo"))
        connection.execute(
            text(
                "CREATE TABLE dbo.XSTHDH (DjLsh int,BJDH text,DWBM text,DDBH text,"
                "CJRQ text,DDZT text,ZJE numeric)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE dbo.XSTHDB (DjLsh int,DjBth int,SPBM text,SPMC text,"
                "DW text,SL numeric,JE numeric)"
            )
        )
        connection.execute(text("CREATE TABLE dbo.XSDDH (DjLsh int,BJDH text,DWBM text,DWMC text)"))
        connection.execute(
            text("INSERT INTO dbo.XSTHDH VALUES (1,'R1','BUYER-A','S1','2026-09-02','订单完成',10)")
        )
        connection.execute(text("INSERT INTO dbo.XSTHDB VALUES (1,1,'P1','Synthetic','box',1,10)"))
        if original_buyer:
            connection.execute(
                text("INSERT INTO dbo.XSDDH VALUES (1,'S1',:buyer,'Synthetic')"),
                {"buyer": original_buyer},
            )

        with pytest.raises(SourceDataError, match="关联|一致"):
            _read_documents(SqliteDialect(connection, scope), report, "returns", 50, 200)
    engine.dispose()


def test_scoped_dispatch_missing_owner_and_date_cannot_be_reported_as_zero(report):
    engine = create_engine("sqlite://")
    scope = DataScope(buyer_codes=["BUYER-A"])
    report = report.model_copy(
        update={"metadata": report.metadata.model_copy(update={"scope": scope})}
    )
    with engine.begin() as connection:
        connection.execute(text("ATTACH DATABASE ':memory:' AS dbo"))
        connection.execute(
            text(
                "CREATE TABLE dbo.CKFHQRH (DjLsh int,BJDH text,SHRQ text,"
                "DDZT text,CKLX text,ZJE numeric)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE dbo.CKFHQRB (DjLsh int,DjBth int,SPBM text,SPMC text,"
                "DW text,SL numeric,JE numeric)"
            )
        )
        connection.execute(text("CREATE TABLE dbo.XSDDH (DjLsh int,BJDH text,DWBM text,DWMC text)"))
        connection.execute(
            text("INSERT INTO dbo.CKFHQRH VALUES (1,'S-MISSING',NULL,'已确认','售出',10)")
        )
        with pytest.raises(SourceDataError, match="关联"):
            _read_documents(SqliteDialect(connection, scope), report, "shipping", 50, 200)
    engine.dispose()
