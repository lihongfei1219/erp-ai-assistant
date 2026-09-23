"""Serve synthetic records through the real backend for browser tests; no ERP connection."""
import os
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

import pandas as pd
import uvicorn

from app.analysis.sales import analyze_sales
from app.connectors.qy import SalesExtract
from app.core.business_rules import load_business_rules
from app.core.settings import ApiSettings
from app.main import create_app
from app.schemas.sales import AnalysisWindow, DataScope

rows = []
lines = []
for index in range(1, 13):
    status = "订单完成" if index <= 10 else "已出库" if index == 11 else "已退回"
    amount = Decimal("100.0000") if index <= 10 else Decimal("200.0000") if index == 11 else Decimal("90.0000")
    rows.append({"order_id": index, "order_number": f"DEMO-{index:03d}", "buyer_code": f"BUYER-{index % 2}",
                 "buyer_name": f"示例采购企业 {index % 2 + 1}", "created_at": f"2026-09-{index:02d}",
                 "status": status, "amount": amount})
    lines.append({"order_id": index, "line_id": 1, "product_code": f"SKU-{index % 3}",
                  "product_name": f"示例商品 {index % 3 + 1}", "quantity": 1,
                  "unit_price": amount, "amount": amount})

report = analyze_sales(SalesExtract(pd.DataFrame(rows), pd.DataFrame(lines), 12, Decimal("1290"), 0),
    AnalysisWindow(start="2026-09-01", end="2026-09-17"), DataScope(all_buyers=True),
    source_as_of=datetime.fromisoformat("2026-09-16T14:33:41+08:00"),
    rules=load_business_rules(), synthetic=True)

os.environ["FEISHU_WEBHOOK_URL"] = ""
os.environ["FEISHU_WEBHOOK_SECRET"] = ""
os.environ["ERP_AI_ENABLED"] = "0"
os.environ["ERP_GRAPH_STATE_DIR"] = str(
    Path(__file__).resolve().parents[2] / ".local" / "browser-graph" / uuid4().hex
)
from operations_fixture import with_operations
report = with_operations(report)
application = create_app(ApiSettings(), report=report)
uvicorn.run(application, host="127.0.0.1", port=8765, log_level="warning", access_log=False)
