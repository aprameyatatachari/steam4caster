from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import auth, fx, games, notifications, performance, watchlist

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(games.router)
api_router.include_router(fx.router)
api_router.include_router(watchlist.router)
api_router.include_router(notifications.router)
api_router.include_router(performance.router)
