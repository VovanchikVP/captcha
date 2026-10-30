"""Web Push: подписки, рассылка и фоновое наблюдение за новыми картинками."""

import asyncio
import json
import logging

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pywebpush import WebPushException, webpush_async

from src.configs import settings
from src.services.images import fetch_unanswered_ids

log = logging.getLogger("show-img")

CREATE_SUBSCRIPTIONS_SQL = """
CREATE TABLE IF NOT EXISTS push_subscription (
    endpoint   text PRIMARY KEY,
    p256dh     text NOT NULL,
    auth       text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
)
"""


async def subscribe(pool: AsyncConnectionPool, endpoint: str, p256dh: str, auth: str) -> None:
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(CREATE_SUBSCRIPTIONS_SQL)
        # браузер может переоформить подписку с тем же endpoint и новыми
        # ключами — перезаписываем, а не плодим дубли
        await cur.execute(
            """
                INSERT INTO push_subscription (endpoint, p256dh, auth)
                VALUES (%s, %s, %s)
                ON CONFLICT (endpoint)
                DO UPDATE SET p256dh = EXCLUDED.p256dh, auth = EXCLUDED.auth
                """,
            (endpoint, p256dh, auth),
        )


async def unsubscribe(pool: AsyncConnectionPool, endpoint: str) -> None:
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(CREATE_SUBSCRIPTIONS_SQL)
        await cur.execute("DELETE FROM push_subscription WHERE endpoint = %s", (endpoint,))


async def watch_new_images(pool: AsyncConnectionPool):
    """Следит за появлением новых неотвеченных картинок и шлёт push.

    В таблице нет ни счётчика, ни времени создания (id — uuid), поэтому
    «новое» считаем диффом множества id между опросами.
    """
    known: set[str] | None = None

    while True:
        await asyncio.sleep(settings.watch_interval_sec)
        try:
            current = await fetch_unanswered_ids(pool)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # база могла уехать — ждём следующего круга, состояние не трогаем
            log.warning("Не удалось опросить картинки: %s", exc)
            continue

        if known is None:
            # первый успешный проход: всё, что уже лежит в базе, не новость
            known = current
            continue

        added = current - known
        known = current
        if added:
            await send_push(pool, len(added))


async def send_push(pool: AsyncConnectionPool, count: int):
    """Рассылает уведомление всем подписчикам, попутно убирая мёртвые подписки."""
    try:
        async with (
            pool.connection(timeout=5) as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(CREATE_SUBSCRIPTIONS_SQL)
            await cur.execute("SELECT endpoint, p256dh, auth FROM push_subscription")
            subscriptions = await cur.fetchall()
    except Exception as exc:
        log.warning("Не удалось прочитать подписки: %s", exc)
        return

    if not subscriptions:
        return

    payload = json.dumps(
        {
            "title": "Новая капча",
            "body": plural_images(count),
            "count": count,
        },
        ensure_ascii=False,
    )

    results = await asyncio.gather(
        *(push_one(row, payload) for row in subscriptions),
        return_exceptions=True,
    )

    dead = [row["endpoint"] for row, ok in zip(subscriptions, results, strict=True) if ok is False]
    if dead:
        await drop_subscriptions(pool, dead)


async def push_one(row: dict, payload: str) -> bool | None:
    """True — доставлено, False — подписка мертва и её надо удалить, None — временная ошибка."""
    subscription = {
        "endpoint": row["endpoint"],
        "keys": {"p256dh": row["p256dh"], "auth": row["auth"]},
    }
    try:
        await webpush_async(
            subscription,
            data=payload,
            vapid_private_key=settings.vapid_private_key,
            vapid_claims={"sub": settings.vapid_subject},
            ttl=600,
            timeout=10,
        )
        return True
    except WebPushException as exc:
        status = getattr(exc.response, "status", None)
        # 404/410 — подписку отозвали на устройстве, чинить нечего
        if status in (404, 410):
            return False
        log.warning("Push не доставлен (%s): %s", status, exc)
        return None
    except Exception as exc:
        log.warning("Push не доставлен: %s", exc)
        return None


async def drop_subscriptions(pool: AsyncConnectionPool, endpoints: list[str]):
    try:
        async with pool.connection(timeout=5) as conn, conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM push_subscription WHERE endpoint = ANY(%s)", (endpoints,)
            )
        log.info("Удалено протухших подписок: %d", len(endpoints))
    except Exception as exc:
        log.warning("Не удалось удалить подписки: %s", exc)


def plural_images(count: int) -> str:
    """«1 новая картинка» / «2 новые картинки» / «5 новых картинок»."""
    tail_100 = count % 100
    tail_10 = count % 10
    if 11 <= tail_100 <= 14:
        word = "новых картинок"
    elif tail_10 == 1:
        word = "новая картинка"
    elif 2 <= tail_10 <= 4:
        word = "новые картинки"
    else:
        word = "новых картинок"
    return f"{count} {word} без ответа"
