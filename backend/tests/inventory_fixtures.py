from decimal import Decimal

from app.schemas.operations import OperationsSnapshot
from tests.growth_fixtures import part, with_cost


def add_risk(report):
    data = report.operations.model_dump()
    day = report.metadata.source_as_of.date()
    data["shipping"] = with_cost(
        part(day.replace(day=1).isoformat(), day.isoformat(), [("A", "30")], 500)
    )
    records = [
        dict(
            record_id=i,
            product_code="P",
            product_name="合成商品",
            specification="合成规格",
            manufacturer="合成厂家",
            unit="盒",
            batch_code=f"B{i}",
            quantity=Decimal(q),
            purchase_unit_cost=Decimal(2),
            received_date=received,
            expiry_date=expiry,
        )
        for i, q, received, expiry in (
            (1, "10", "2026-06-01", "2026-09-21"),
            (2, "20", "2026-09-01", "2026-10-06"),
            (3, "5", "2026-06-01", "2026-09-15"),
        )
    ]
    data["shipping_history"].append(with_cost(part("2025-09-01", "2025-10-01", [("A", "60")], 600)))
    historical = data["shipping_history"][-1].model_dump()
    historical["documents"][0]["occurred_at"] = historical["documents"][0]["occurred_at"].replace(
        day=20
    )
    data["shipping_history"][-1] = historical
    data["inventory"] = dict(
        as_of=report.metadata.source_as_of,
        records=records,
        control_record_count=3,
        control_quantities={"盒": Decimal(35)},
        risk_ready=True,
        control_known_cost=Decimal(70),
    )
    return report.model_copy(update={"operations": OperationsSnapshot.model_validate(data)})
