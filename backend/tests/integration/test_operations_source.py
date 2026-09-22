import os

import pytest

from app.connectors.qy import make_source_engine
from app.core.settings import source_odbc

pytestmark = pytest.mark.skipif(
    os.getenv("ERP_RUN_INTEGRATION") != "1", reason="Source read-only opt-in"
)


def test_operations_source_reconciles_all_domains(report):
    from app.connectors.operations import read_operations

    engine = make_source_engine(source_odbc())
    try:
        facts = read_operations(engine, report)
        assert facts.returns is not None and facts.shipping is not None
        assert facts.inventory is not None
        assert facts.returns.control_document_count == len(facts.returns.documents)
        assert facts.shipping.control_document_count == len(facts.shipping.documents)
        assert facts.inventory.control_record_count == len(facts.inventory.records)
        assert facts.source_as_of == report.metadata.source_as_of
    finally:
        engine.dispose()
