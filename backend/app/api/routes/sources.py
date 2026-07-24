"""sources 模块端点（详细设计 1.5）。"""
import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import ROLE_REGISTERED, require_role
from app.collector.governance import Governance
from app.core.errors import CODE_NOT_FOUND, CODE_STATE_INVALID, BizError, ok
from app.db.redis_client import get_cache_redis
from app.db.session import get_db
from app.models.user import User
from app.repositories.audit_repo import write_audit
from app.schemas.source import CrawlPreviewRequest, SourceCreate, SourceUpdate
from app.services.source_service import COVERAGE_METHODOLOGY, SourceService, submit_verify_job

router = APIRouter()


def _get_source_or_404(db: Session, source_id: uuid.UUID):
    source = SourceService(db).repo.get(source_id)
    if source is None:
        raise BizError(CODE_NOT_FOUND, "媒体源不存在")
    return source


@router.get("")
def list_sources(
    country_code: str | None = Query(default=None, min_length=2, max_length=2),
    status: str | None = Query(default=None, pattern="^(active|degraded|failed)$"),
    collect_mode: str | None = Query(default=None, pattern="^(rss|rsshub|gdelt)$"),
    is_custom: bool | None = None,
    keyword: str | None = None,
    sort: str = Query(default="audience_weight_desc", pattern="^(audience_weight_desc|name|last_success_at)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REGISTERED)),
):
    service = SourceService(db)
    total, items = service.repo.list(
        country_code=country_code, status=status, collect_mode=collect_mode,
        is_custom=is_custom, keyword=keyword, sort=sort, page=page, page_size=page_size,
    )
    data: dict = {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [service.to_list_item(s) for s in items],
    }
    if country_code:
        summary = next((r for r in service.coverage_items() if r["country_code"] == country_code.upper()), None)
        if summary:
            data["country_summary"] = {
                "country_code": summary["country_code"],
                "total_audience_share": summary["total_audience_share"],
                "coverage_confidence": summary["coverage_confidence"],
                "coverage_gap": summary["coverage_gap"],
            }
    return ok(data)


@router.get("/coverage")
def coverage(
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REGISTERED)),
):
    service = SourceService(db)
    return ok({"methodology": COVERAGE_METHODOLOGY, "items": service.coverage_items()})


@router.get("/{source_id}")
def source_detail(
    source_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REGISTERED)),
):
    source = _get_source_or_404(db, source_id)
    service = SourceService(db)
    data = service.to_list_item(source)
    data.update({
        "homepage_url": source.homepage_url,
        "feed_url": source.feed_url,
        "crawl_config": source.crawl_config if user.role == "admin" else None,
        "consecutive_failures": source.consecutive_failures,
        "degraded_since": source.degraded_since.isoformat() if source.degraded_since else None,
        "status_history": source.status_history,
        "created_at": source.created_at.isoformat() if source.created_at else None,
        "updated_at": source.updated_at.isoformat() if source.updated_at else None,
    })
    return ok(data)


@router.post("")
def create_source(
    body: SourceCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    service = SourceService(db)
    source = service.create(body)
    write_audit(db, "source.create", user=user, resource=f"sources/{source.id}",
                detail={"name": source.name, "country_code": source.country_code},
                ip=request.client.host if request.client else None)
    db.commit()
    return ok({"id": str(source.id), "status": source.status})


@router.put("/{source_id}")
def update_source(
    source_id: uuid.UUID,
    body: SourceUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    service = SourceService(db)
    source = _get_source_or_404(db, source_id)
    before = {"status": source.status, "poll_interval_min": source.poll_interval_min,
              "crawl_config": source.crawl_config}
    service.update(source, body)
    after = {"status": source.status, "poll_interval_min": source.poll_interval_min,
             "crawl_config": source.crawl_config}
    write_audit(db, "source.update", user=user, resource=f"sources/{source.id}",
                detail={"before": before, "after": after},
                ip=request.client.host if request.client else None)
    db.commit()
    # 热更新信号（Pub/Sub 通知采集 worker；调度器每 tick 亦重读 DB，双保险）
    try:
        get_cache_redis().publish("sources:reload", str(source.id))
    except Exception:  # noqa: BLE001 信号失败不影响 DB 配置生效
        pass
    return ok({"id": str(source.id), "status": source.status})


@router.post("/{source_id}/verify")
def verify_source(
    source_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    from datetime import datetime, timezone

    source = _get_source_or_404(db, source_id)
    if source.status != "failed":
        raise BizError(CODE_STATE_INVALID, "仅 failed 状态的源可发起人工重验证")
    gov = Governance(db, get_cache_redis())
    job = gov.create_job(source.id, source.collect_mode, datetime.now(timezone.utc))
    write_audit(db, "source.verify", user=user, resource=f"sources/{source.id}",
                ip=request.client.host if request.client else None)
    db.commit()
    submit_verify_job(source, job)
    return ok({"job_id": str(job.id), "message_hint": "验证任务已提交，结果异步更新"})


@router.post("/crawl-preview")
def crawl_preview(
    body: CrawlPreviewRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REGISTERED)),
):
    result = SourceService(db).crawl_preview(body.url, body.adapter_type, body.crawl_config)
    return ok(result)
