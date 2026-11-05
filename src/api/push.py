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


@router.get("/status")
async def push_status(pool: Pool):
    """Куда смотреть, когда уведомления «не приходят»: ключи, подписки, наблюдатель."""
    state = push.watch_state

    try:
        subscriptions = await push.count_subscriptions(pool)
        subscriptions_error = None
    except Exception as exc:
        subscriptions = None
        subscriptions_error = f"{type(exc).__name__}: {exc}"

    return {
        "push_enabled": settings.push_enabled,
        "vapid_public_key_set": bool(settings.vapid_public_key),
        "vapid_private_key_set": bool(settings.vapid_private_key),
        "vapid_subject": settings.vapid_subject,
        "watch_interval_sec": settings.watch_interval_sec,
        "watch_limit": settings.watch_limit,
        "subscriptions": subscriptions,
        "subscriptions_error": subscriptions_error,
        "watcher": {
            "started": state.started,
            "last_poll": state.last_poll,
            "last_error": state.last_error,
            # сколько картинок без ответа видел наблюдатель в последний опрос;
            # если это число упёрлось в watch_limit, новые id могут не попадать
            # в окно сравнения — тогда уведомления будут приходить через раз
            "known_count": state.known_count,
            "last_new_count": state.last_new_count,
            "last_push": state.last_push,
            "last_push_results": state.last_push_results,
        },
    }


@router.post("/test")
async def push_test(pool: Pool):
    """Шлёт пробное уведомление всем подписчикам и возвращает ответ push-сервиса.

    Нужно, чтобы отделить «сервер не достучался до FCM» от «уведомление пришло,
    но телефон его не показал»: без этого между событием в базе и экраном
    телефона три звена и ни одно не видно.
    """
    if not settings.push_enabled:
        raise HTTPException(status_code=503, detail="Уведомления не настроены")

    try:
        subscriptions = await push.list_subscriptions(pool)
        subscriptions_error = None
    except Exception as exc:
        subscriptions = []
        subscriptions_error = f"{type(exc).__name__}: {exc}"

    first_endpoint = subscriptions[0]["endpoint"] if subscriptions else None
    service = await push.check_push_service(first_endpoint)

    results = await push.broadcast(
        pool,
        title="Проверка связи",
        body="Если вы это видите — push доходит до устройства",
        count=0,
    )
    return {
        "push_service": service,
        "subscriptions": len(subscriptions),
        "subscriptions_error": subscriptions_error,
        "sent": len(results),
        "delivered": sum(1 for r in results if r.ok),
        "results": [push.result_to_dict(r) for r in results],
    }
