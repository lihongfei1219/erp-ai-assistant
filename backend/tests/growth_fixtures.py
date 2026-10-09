from decimal import Decimal

from app.schemas.operations import DocumentFacts


def with_cost(facts, unit_cost="0.5"):
    data = facts.model_dump()
    total = Decimal(0)
    for doc in data["documents"]:
        for line in doc["lines"]:
            line["purchase_unit_cost"] = Decimal(unit_cost)
            total += line["quantity"] * Decimal(unit_cost)
    data.update(cost_ready=True, control_purchase_cost=total)
    return DocumentFacts.model_validate(data)


def part(start, end, entries, offset=0, unit="盒"):
    docs = []
    for i, (buyer, amount) in enumerate(entries, offset + 1):
        docs.append(
            dict(
                document_id=i,
                document_number=f"D-{i}",
                buyer_code=buyer,
                buyer_name=buyer,
                original_order_id=i,
                original_order_number=f"S-{i}",
                occurred_at=f"{start}T12:00:00+08:00",
                status="已确认",
                amount=amount,
                lines=[
                    dict(
                        line_id=1,
                        product_code="P",
                        product_name="合成商品",
                        specification="合成规格",
                        manufacturer="合成厂家",
                        unit=unit,
                        quantity=amount,
                        amount=amount,
                    )
                ],
            )
        )
    total = sum(Decimal(v) for _, v in entries)
    return DocumentFacts(
        start=start,
        end_exclusive=end,
        time_basis="出库确认日期",
        source_tables=["CKFHQRH", "CKFHQRB"],
        included_statuses=["已确认"],
        excluded_document_count=0,
        documents=docs,
        comparison_ready=True,
        control_document_count=len(docs),
        control_line_count=len(docs),
        control_amount=total,
        control_quantities={unit: total} if docs else {},
    )
