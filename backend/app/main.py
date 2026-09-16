import secrets
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles

from app.core.reports import load_report
from app.core.settings import ApiSettings
from app.schemas.sales import (
    Breakdown,
    DailyPoint,
    OperatingAnalysis,
    OrderEvidence,
    Quality,
    ReportMetadata,
    RunStats,
    SalesReport,
    StatusPoint,
    StrictModel,
    Summary,
)


class DashboardResponse(StrictModel):
    metadata: ReportMetadata
    summary: Summary
    quality: Quality


class StatusResponse(StrictModel):
    metadata: ReportMetadata
    quality: Quality
    stats: RunStats


class TrendResponse(StrictModel):
    metadata: ReportMetadata
    items: list[DailyPoint]


class BreakdownResponse(StrictModel):
    metadata: ReportMetadata
    dimension: Literal["buyer", "product"]
    total_groups: int
    items: list[Breakdown]


class StatesResponse(StrictModel):
    metadata: ReportMetadata
    items: list[StatusPoint]


class EvidenceListResponse(StrictModel):
    metadata: ReportMetadata
    total: int
    page: int
    page_size: int
    items: list[OrderEvidence]


class EvidenceResponse(StrictModel):
    metadata: ReportMetadata
    item: OrderEvidence


class OperatingResponse(StrictModel):
    metadata: ReportMetadata
    operating: OperatingAnalysis
    raw_summary: Summary
    statuses: list[StatusPoint]
    quality: Quality
    buyer_group_count: int
    product_group_count: int


def create_app(
    settings: ApiSettings | None = None, *, report: SalesReport | None = None
) -> FastAPI:
    settings = settings or ApiSettings.from_env()
    application = FastAPI(
        title="ERP AI Assistant · 销售订单试点",
        version="0.1.0",
        description=(
            "读取独立分析进程生成的本地快照。所有业务接口需 Bearer 令牌。"
            "令牌绑定当前配置的快照及企业范围，日期范围由生成快照时确定。"
            "订单金额不是支付成交额。"
        ),
    )
    configured_report = report
    if configured_report is None and settings.report_path is not None:
        try:
            configured_report = load_report(Path(settings.report_path))
        except Exception:
            # Fail closed without leaking source paths, order values or validation input.
            configured_report = None

    security = HTTPBearer(auto_error=False)

    def authorize(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    ):
        if len(settings.token) < 32:
            raise HTTPException(503, "未配置足够强度的开发访问令牌")
        if credentials is None or not secrets.compare_digest(
            credentials.credentials.encode("utf-8"), settings.token.encode("utf-8")
        ):
            raise HTTPException(401, "需要有效的访问令牌", headers={"WWW-Authenticate": "Bearer"})

    def get_report() -> SalesReport:
        if configured_report is None:
            raise HTTPException(503, "分析快照未就绪，请先运行后台分析命令")
        return configured_report

    Report = Annotated[SalesReport, Depends(get_report)]

    def reject_unknown_filters(request: Request):
        allowed = {
            "/api/v1/orders": {"page", "page_size", "view"},
            "/api/v1/sales/breakdown": {"dimension", "limit"},
        }.get(request.url.path, set())
        if set(request.query_params) - allowed:
            raise HTTPException(422, "包含不支持的筛选参数；日期和企业范围由当前快照确定")

    router = APIRouter(
        prefix="/api/v1", dependencies=[Depends(authorize), Depends(reject_unknown_filters)]
    )

    @application.middleware("http")
    async def private_responses(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @application.get("/healthz")
    def health():
        return {"status": "ok", "mode": "development_snapshot"}

    @router.get("/data/status", response_model=StatusResponse)
    def status(result: Report):
        return StatusResponse(metadata=result.metadata, quality=result.quality, stats=result.stats)

    @router.get("/dashboard/summary", response_model=DashboardResponse)
    def summary(result: Report):
        return DashboardResponse(
            metadata=result.metadata,
            summary=result.summary,
            quality=result.quality,
        )

    @router.get("/dashboard/operating", response_model=OperatingResponse)
    def operating_dashboard(result: Report):
        if result.operating is None:
            raise HTTPException(503, "当前快照尚未包含经营规则，请重新生成分析快照")
        view = result.operating.model_copy(
            update={
                "buyers": result.operating.buyers[:10],
                "products": result.operating.products[:10],
            }
        )
        return OperatingResponse(
            metadata=result.metadata,
            operating=view,
            raw_summary=result.summary,
            statuses=result.statuses,
            quality=result.quality,
            buyer_group_count=len(result.operating.buyers),
            product_group_count=len(result.operating.products),
        )

    @router.get("/business/assumptions", response_class=FileResponse)
    def business_assumptions():
        document = Path(__file__).resolve().parents[2] / "docs" / "business-assumptions.md"
        return FileResponse(
            document, media_type="text/markdown; charset=utf-8", filename="business-assumptions.md"
        )

    @router.get("/sales/trends", response_model=TrendResponse)
    def trends(result: Report):
        return TrendResponse(metadata=result.metadata, items=result.daily)

    @router.get("/sales/breakdown", response_model=BreakdownResponse)
    def breakdown(
        result: Report,
        dimension: Literal["buyer", "product"] = "buyer",
        limit: int = Query(default=10, ge=1, le=100),
    ):
        items = result.buyers if dimension == "buyer" else result.products
        return BreakdownResponse(
            metadata=result.metadata,
            dimension=dimension,
            total_groups=len(items),
            items=items[:limit],
        )

    @router.get("/sales/states", response_model=StatesResponse)
    def states(result: Report):
        return StatesResponse(metadata=result.metadata, items=result.statuses)

    @router.get("/orders", response_model=EvidenceListResponse)
    def orders(
        result: Report,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        view: Literal["all", "operating"] = "all",
    ):
        rows = result.evidence
        if view == "operating":
            if result.operating is None:
                raise HTTPException(503, "当前快照尚未包含经营规则")
            rows = [row for row in rows if row.status in result.operating.policy.included_statuses]
        start = (page - 1) * page_size
        return EvidenceListResponse(
            metadata=result.metadata,
            total=len(rows),
            page=page,
            page_size=page_size,
            items=rows[start : start + page_size],
        )

    @router.get("/orders/{order_id}", response_model=EvidenceResponse)
    def order(order_id: int, result: Report):
        item = next((row for row in result.evidence if row.order_id == order_id), None)
        if item is None:
            raise HTTPException(404, "订单不在当前快照范围内")
        return EvidenceResponse(metadata=result.metadata, item=item)

    application.include_router(router)
    frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend_dist.is_dir():
        application.mount("/", StaticFiles(directory=frontend_dist, html=True), name="web")
    return application


app = create_app()
