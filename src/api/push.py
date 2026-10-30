"""Ручки подписки на Web Push."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from psycopg_pool import AsyncConnectionPool

from src.api.deps import get_pool
from src.configs import settings
from src.models import EndpointIn, SubscriptionIn
from src.services import push

router = APIRouter(prefix="/api/push", tags=["push"])

Pool = Annotated[AsyncConnectionPool, Depends(get_pool)]


@router.get("/public-key")
async def push_public_key():
    """Публичный VAPID-ключ для PushManager.subscribe(). Пустой — значит push выключен."""
    return {"key": settings.vapid_public_key if settings.push_enabled else ""}


@router.post("/subscribe")
async def push_subscribe(sub: SubscriptionIn, pool: Pool):
    if not settings.push_enabled:
        raise HTTPException(status_code=503, detail="Уведомления не настроены")

    await push.subscribe(pool, sub.endpoint, sub.keys.p256dh, sub.keys.auth)
    return {"status": "ok"}


@router.post("/unsubscribe")
async def push_unsubscribe(payload: EndpointIn, pool: Pool):
    await push.unsubscribe(pool, payload.endpoint)
    return {"status": "ok"}
