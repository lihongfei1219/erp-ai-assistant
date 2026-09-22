"""Synthetic domain facts shared by browser fixture server; never reads the source database."""

from app.schemas.operations import OperationsSnapshot


def with_operations(report):
    def facts(domain):
        status = "订单完成" if domain == "returns" else "已确认"
        return dict(
            start="2026-09-01",
            end_exclusive="2026-09-16",
            time_basis="退货单创建日期" if domain == "returns" else "销售出库确认日期",
            source_tables=["synthetic_" + domain],
            included_statuses=[status],
            excluded_document_count=0,
            control_document_count=1,
            control_line_count=1,
            control_amount="10",
            control_quantities={"盒": "2"},
            documents=[
                dict(
                    document_id=1,
                    document_number="DEMO-" + domain,
                    buyer_code="BUYER-0",
                    buyer_name="示例客户",
                    original_order_id=1,
                    original_order_number="DEMO-001",
                    occurred_at="2026-09-15T10:00:00+08:00",
                    status=status,
                    amount="10",
                    lines=[
                        dict(
                            line_id=1,
                            product_code="SKU-0",
                            product_name="示例商品",
                            unit="盒",
                            quantity="2",
                            amount="10",
                        )
                    ],
                )
            ],
        )

    operations = OperationsSnapshot(
        source_as_of=report.metadata.source_as_of,
        all_buyers=True,
        returns=facts("returns"),
        shipping=facts("shipping"),
        inventory=dict(
            as_of=report.metadata.source_as_of,
            control_record_count=1,
            control_quantities={"盒": "12"},
            records=[
                dict(
                    record_id=1,
                    product_code="SKU-0",
                    product_name="示例商品",
                    unit="盒",
                    batch_code="B1",
                    quantity="12",
                )
            ],
        ),
    )
    return report.model_copy(update={"operations": operations})
