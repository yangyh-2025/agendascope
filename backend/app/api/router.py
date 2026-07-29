"""/api/v1 路由聚合（各业务模块在此注册）。"""
from fastapi import APIRouter

from app.api.routes import (
    agenda_events,
    alert_rules,
    alerts,
    articles,
    audit,
    auth,
    persons_orgs,
    reports,
    setup,
    snapshots,
    sources,
    subscriptions,
    system_admin,
    topics,
)
from app.api.routes import (
    map as map_route,
)

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(sources.router, prefix="/sources", tags=["sources"])
api_router.include_router(topics.router, prefix="/topics", tags=["topics"])
api_router.include_router(agenda_events.router, prefix="/agenda-events", tags=["agenda-events"])
api_router.include_router(articles.router, prefix="/articles", tags=["articles"])
api_router.include_router(persons_orgs.router, prefix="/persons-orgs", tags=["persons-orgs"])
api_router.include_router(snapshots.router, prefix="/snapshots", tags=["snapshots"])
api_router.include_router(map_route.router, prefix="/map", tags=["map"])
api_router.include_router(alert_rules.router, prefix="/alert-rules", tags=["alert-rules"])
api_router.include_router(alerts.router, prefix="/alerts", tags=["alerts"])
api_router.include_router(subscriptions.router, prefix="/subscriptions", tags=["subscriptions"])
api_router.include_router(reports.router, prefix="/report-exports", tags=["report-exports"])
api_router.include_router(setup.router, prefix="/setup", tags=["setup"])
api_router.include_router(system_admin.router, prefix="/system", tags=["system"])
api_router.include_router(audit.router, prefix="/system/audit-logs", tags=["system"])
