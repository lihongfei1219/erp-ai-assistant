"""Bounded fixed SELECTs for the verified QY business tables. ERP source is read-only."""

from collections import defaultdict
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import text

from app.connectors.qy import SourceDataError, _query
from app.schemas.operations import DocumentFacts, InventoryFacts, OperationsSnapshot


def _read_documents(connection, report, domain, max_documents, max_lines):
    returns = domain == "returns"
    header, detail = ("XSTHDH", "XSTHDB") if returns else ("CKFHQRH", "CKFHQRB")
    date_field = "CJRQ" if returns else "SHRQ"
    order_field = "DDBH" if returns else "BJDH"
    status = "订单完成" if returns else "已确认"
    scope, window = report.metadata.scope, report.metadata.window
    params = dict(
        start=window.start,
        end=window.end,
        status=status,
    )
    base = f"h.{date_field} >= :start AND h.{date_field} < :end"
    if not returns:
        base += " AND h.CKLX = N'售出'"
    if not scope.all_buyers:
        base += " AND h.DWBM IN :buyer_codes" if returns else " AND s.DWBM IN :buyer_codes"
        params["buyer_codes"] = list(scope.buyer_codes)
    relation = f"FROM dbo.{header} h LEFT JOIN dbo.XSDDH s ON s.BJDH=h.{order_field}"
    predicate = base + " AND h.DDZT = :status"
    owner_filter = ""
    if not scope.all_buyers:
        owner_filter = "AND h.DWBM IN :buyer_codes" if returns else "AND s.DWBM IN :buyer_codes"
    # Do not silently drop eligible records missing event dates or original-order links.
    invalid = connection.execute(
        _query(
            f"""
        SELECT COUNT(*) FROM dbo.{header} h LEFT JOIN dbo.XSDDH s ON s.BJDH=h.{order_field}
        WHERE h.DDZT=:status AND h.{date_field} IS NULL
        {"AND h.CKLX=N'售出'" if not returns else ""}
        {owner_filter}
    """,
            scope,
        ),
        params,
    ).scalar_one()
    if invalid:
        raise SourceDataError("业务单据缺少统计事件日期")
    if not returns:
        # An unlinked dispatch cannot safely be attributed to any authorized customer.
        # Reject unknown ownership before scoped totals, rather than silently reporting zero.
        missing_owner = connection.execute(
            text(f"""
            SELECT COUNT(*) {relation}
            WHERE (h.{date_field} IS NULL OR (h.{date_field} >= :start AND h.{date_field} < :end))
            AND h.CKLX=N'售出' AND h.DDZT=:status AND s.DjLsh IS NULL
        """),
            params,
        ).scalar_one()
        if missing_owner:
            raise SourceDataError("销售出库单未能关联原销售订单，无法核验授权范围")
    extra = ", RTRIM(h.DWBM) AS declared_buyer_code" if returns else ""
    headers = (
        connection.execute(
            _query(
                f"""
        SELECT TOP ({max_documents + 1}) h.DjLsh document_id, RTRIM(h.BJDH) document_number,
        RTRIM(s.DWBM) buyer_code, RTRIM(s.DWMC) buyer_name, s.DjLsh original_order_id,
        RTRIM(s.BJDH) original_order_number, h.{date_field} occurred_at,
        RTRIM(h.DDZT) status, h.ZJE amount {extra}
        {relation} WHERE {predicate} ORDER BY h.DjLsh
    """,
                scope,
            ),
            params,
        )
        .mappings()
        .all()
    )
    lines = (
        connection.execute(
            _query(
                f"""
        SELECT TOP ({max_lines + 1}) b.DjLsh document_id,b.DjBth line_id,
        RTRIM(b.SPBM) product_code,RTRIM(b.SPMC) product_name,
        RTRIM(b.DW) unit,b.SL quantity,b.JE amount
        FROM dbo.{detail} b JOIN dbo.{header} h ON h.DjLsh=b.DjLsh
        LEFT JOIN dbo.XSDDH s ON s.BJDH=h.{order_field}
        WHERE {predicate} ORDER BY b.DjLsh,b.DjBth
    """,
                scope,
            ),
            params,
        )
        .mappings()
        .all()
    )
    if len(headers) > max_documents or len(lines) > max_lines:
        raise SourceDataError("业务单据或明细超过读取上限，请缩小日期或范围")
    control = (
        connection.execute(
            _query(
                f"""
        SELECT COUNT_BIG(*) n,COALESCE(SUM(h.ZJE),0) amount {relation} WHERE {predicate}
    """,
                scope,
            ),
            params,
        )
        .mappings()
        .one()
    )
    unit_controls = (
        connection.execute(
            _query(
                f"""
        SELECT RTRIM(b.DW) unit,COUNT_BIG(*) n,SUM(CAST(b.SL AS decimal(28,4))) quantity
        FROM dbo.{detail} b JOIN dbo.{header} h ON h.DjLsh=b.DjLsh
        LEFT JOIN dbo.XSDDH s ON s.BJDH=h.{order_field}
        WHERE {predicate} GROUP BY RTRIM(b.DW)
    """,
                scope,
            ),
            params,
        )
        .mappings()
        .all()
    )
    excluded = connection.execute(
        _query(
            f"""
        SELECT COUNT_BIG(*) {relation} WHERE {base} AND (h.DDZT<>:status OR h.DDZT IS NULL)
    """,
            scope,
        ),
        params,
    ).scalar_one()
    by_id = defaultdict(list)
    for row in lines:
        value = dict(row)
        by_id[value.pop("document_id")].append(value)
    tz = ZoneInfo(
        report.operating.policy.business_timezone if report.operating else "Asia/Shanghai"
    )
    documents = []
    for row in headers:
        value = dict(row)
        if returns and value.pop("declared_buyer_code") != value["buyer_code"]:
            raise SourceDataError("退货单客户与原销售单不一致")
        if value["original_order_id"] is None:
            raise SourceDataError("业务单据未能关联原销售订单")
        value["occurred_at"] = value["occurred_at"].replace(tzinfo=tz)
        value["lines"] = by_id[value["document_id"]]
        documents.append(value)
    return DocumentFacts(
        start=window.start,
        end_exclusive=window.end,
        time_basis="已完成退货单创建日期" if returns else "已确认销售出库确认日期",
        source_tables=[header, detail],
        included_statuses=[status],
        excluded_document_count=excluded,
        documents=documents,
        control_document_count=control["n"],
        control_line_count=sum(r["n"] for r in unit_controls),
        control_amount=control["amount"],
        control_quantities={r["unit"]: r["quantity"] for r in unit_controls},
    )


def _read_inventory(connection, report, max_records):
    rows = (
        connection.execute(
            text(f"""
        SELECT TOP ({max_records + 1}) s.DjLsh record_id,RTRIM(s.SPBM) product_code,
        RTRIM(p.SPMC) product_name,RTRIM(p.DW) unit,
        COALESCE(RTRIM(s.SPPC),'') batch_code,s.KCSL quantity
        FROM dbo.SPPHGLBH s LEFT JOIN dbo.HGJYSPDAH p ON s.SPBM=p.SPBM ORDER BY s.DjLsh
    """)
        )
        .mappings()
        .all()
    )
    if len(rows) > max_records:
        raise SourceDataError("库存记录超过读取上限")
    controls = (
        connection.execute(
            text("""
        SELECT RTRIM(p.DW) unit,COUNT_BIG(*) n,SUM(CAST(s.KCSL AS decimal(28,4))) quantity
        FROM dbo.SPPHGLBH s LEFT JOIN dbo.HGJYSPDAH p ON s.SPBM=p.SPBM GROUP BY RTRIM(p.DW)
    """)
        )
        .mappings()
        .all()
    )
    return InventoryFacts(
        as_of=report.metadata.source_as_of,
        records=[dict(r) for r in rows],
        control_record_count=sum(r["n"] for r in controls),
        control_quantities={r["unit"]: r["quantity"] for r in controls},
    )


def read_operations(engine, report, *, max_documents=50_000, max_lines=200_000):
    if any(type(n) is not int or n <= 0 for n in (max_documents, max_lines)):
        raise ValueError("读取上限必须为正整数")
    try:
        with engine.connect() as connection, connection.begin():
            if connection.execute(text("SELECT DB_NAME()")).scalar_one() != "ERP_Local":
                raise SourceDataError("业务源必须为已授权的 ERP_Local")
            returns = _read_documents(connection, report, "returns", max_documents, max_lines)
            shipping = _read_documents(connection, report, "shipping", max_documents, max_lines)
            inventory = (
                _read_inventory(connection, report, max_lines)
                if report.metadata.scope.all_buyers
                else None
            )
        return OperationsSnapshot(
            source_as_of=report.metadata.source_as_of,
            all_buyers=report.metadata.scope.all_buyers,
            buyer_codes=report.metadata.scope.buyer_codes,
            returns=returns,
            shipping=shipping,
            inventory=inventory,
        )
    except ValidationError:
        raise SourceDataError("业务事实校验或主明细／数量对账未通过，未发布新快照") from None
