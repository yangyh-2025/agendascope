"""/api/v1 路由聚合（各业务模块在此注册）。"""
from fastapi import APIRouter

from app.api.routes import (
    agenda_events,
    articles,
    auth,
    persons_orgs,
    snapshots,
    sources,
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
