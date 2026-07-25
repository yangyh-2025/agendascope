"""/api/v1 路由聚合（各业务模块在此注册）。"""
from fastapi import APIRouter

from app.api.routes import auth, sources, topics

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(sources.router, prefix="/sources", tags=["sources"])
api_router.include_router(topics.router, prefix="/topics", tags=["topics"])
