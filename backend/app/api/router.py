"""/api/v1 路由聚合（各业务模块在此注册）。"""
from fastapi import APIRouter

from app.api.routes import agenda_events, auth, sources, topics

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(sources.router, prefix="/sources", tags=["sources"])
api_router.include_router(topics.router, prefix="/topics", tags=["topics"])
api_router.include_router(agenda_events.router, prefix="/agenda-events", tags=["agenda-events"])
