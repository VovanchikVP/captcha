"""Web Push: подписки, рассылка и фоновое наблюдение за новыми картинками."""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlparse

import aiohttp
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pywebpush import WebPushException, webpush_async

from src.configs import settings
from src.services.images import fetch_unanswered_ids

log = logging.getLogger("show-img")


@dataclass
class PushResult:
    """Чем закончилась отправка одному подписчику."""

    endpoint: str
    ok: bool
    status: int | None = None
    error: str | None = None
    # подписку отозвали на устройстве — её надо удалить
    gone: bool = False


@dataclass
class WatchState:
    """Видимое снаружи состояние фоновой задачи — чтобы не гадать по логам."""

    started: bool = False
    last_poll: datetime | None = None
    last_error: str | None = None
    known_count: int = 0
    last_new_count: int = 0
    last_push: datetime | None = None
    last_push_results: list[dict] = field(default_factory=list)


watch_state = WatchState()

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
    watch_state.started = True
    log.info("Наблюдатель запущен, интервал %s с", settings.watch_interval_sec)

    while True:
        await asyncio.sleep(settings.watch_interval_sec)
        try:
            current = await fetch_unanswered_ids(pool)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # база могла уехать — ждём следующего круга, состояние не трогаем
            watch_state.last_error = f"{type(exc).__name__}: {exc}"
            log.warning("Не удалось опросить картинки: %s", exc)
            continue

        watch_state.last_poll = datetime.now(UTC)
        watch_state.last_error = None
        watch_state.known_count = len(current)

        if known is None:
            # первый успешный проход: всё, что уже лежит в базе, не новость
            known = current
            log.info("Первый опрос: запомнили %d картинок без ответа", len(current))
            continue

        added = current - known
        known = current
        if added:
            watch_state.last_new_count = len(added)
            log.info("Новых картинок: %d — рассылаем push", len(added))
            await send_push(pool, len(added))


async def send_push(pool: AsyncConnectionPool, count: int) -> list[PushResult]:
    """Рассылает уведомление всем подписчикам, попутно убирая мёртвые подписки."""
    return await broadcast(
        pool,
        title="Новая капча",
        body=plural_images(count),
        count=count,
    )


async def broadcast(
    pool: AsyncConnectionPool, title: str, body: str, count: int
) -> list[PushResult]:
    """Отправляет одно уведомление всем подписчикам и возвращает отчёт по каждому."""
    try:
        subscriptions = await list_subscriptions(pool)
    except Exception as exc:
        log.warning("Не удалось прочитать подписки: %s", exc)
        return []

    if not subscriptions:
        log.info("Подписчиков нет — отправлять некому")
        return []

    payload = json.dumps(
        {"title": title, "body": body, "count": count},
        ensure_ascii=False,
    )

    results: list[PushResult] = list(
        await asyncio.gather(*(push_one(row, payload) for row in subscriptions))
    )

    watch_state.last_push = datetime.now(UTC)
    watch_state.last_push_results = [result_to_dict(r) for r in results]

    delivered = sum(1 for r in results if r.ok)
    log.info("Push отправлен: доставлено %d из %d", delivered, len(results))

    dead = [r.endpoint for r in results if r.gone]
    if dead:
        await drop_subscriptions(pool, dead)

    return results


async def list_subscriptions(pool: AsyncConnectionPool) -> list[dict]:
    async with (
        pool.connection(timeout=5) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        await cur.execute(CREATE_SUBSCRIPTIONS_SQL)
        await cur.execute("SELECT endpoint, p256dh, auth FROM push_subscription")
        return await cur.fetchall()


async def count_subscriptions(pool: AsyncConnectionPool) -> int:
    async with pool.connection(timeout=5) as conn, conn.cursor() as cur:
        await cur.execute(CREATE_SUBSCRIPTIONS_SQL)
        await cur.execute("SELECT count(*) FROM push_subscription")
        row = await cur.fetchone()
        return int(row[0]) if row else 0


async def push_one(row: dict, payload: str) -> PushResult:
    """Одна отправка. Исключения не выпускает — результат виден в PushResult."""
    endpoint = row["endpoint"]
    subscription = {
        "endpoint": endpoint,
        "keys": {"p256dh": row["p256dh"], "auth": row["auth"]},
    }
    try:
        response = await webpush_async(
            subscription,
            data=payload,
            vapid_private_key=settings.vapid_private_key,
            vapid_claims={"sub": settings.vapid_subject},
            ttl=600,
            timeout=10,
        )
        return PushResult(endpoint=endpoint, ok=True, status=getattr(response, "status", None))
    except WebPushException as exc:
        status = exc.status_code
        # 404/410 — подписку отозвали на устройстве, чинить нечего
        gone = status in (404, 410)
        if not gone:
            log.warning("Push не доставлен (%s): %s", status, exc)
        return PushResult(endpoint=endpoint, ok=False, status=status, error=str(exc), gone=gone)
    except Exception as exc:
        log.warning("Push не доставлен: %s", exc)
        return PushResult(endpoint=endpoint, ok=False, error=f"{type(exc).__name__}: {exc}")


# куда стучимся, когда подписок ещё нет: Chrome ходит именно сюда
DEFAULT_PUSH_HOST = "https://fcm.googleapis.com"


async def check_push_service(endpoint: str | None = None) -> dict:
    """Достаёт ли под до push-сервиса.

    Отделяет «в кластере нет выхода в интернет» от всех остальных причин:
    в контейнере нет ни curl, ни shell-утилит, проверить иначе нечем.
    Любой HTTP-ответ — успех: нас интересует сеть, а не код ответа.
    """
    origin = DEFAULT_PUSH_HOST
    if endpoint:
        parsed = urlparse(endpoint)
        origin = f"{parsed.scheme}://{parsed.netloc}"

    try:
        async with aiohttp.ClientSession() as session:
            response = await session.get(origin, timeout=aiohttp.ClientTimeout(total=5))
            return {"origin": origin, "reachable": True, "status": response.status}
    except Exception as exc:
        return {
            "origin": origin,
            "reachable": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def result_to_dict(result: PushResult) -> dict:
    """Короткая форма для /api/push/status и /api/push/test.

    endpoint обрезаем: целиком он содержит токен устройства, в выдаче API ему не место.
    """
    return {
        "endpoint": short_endpoint(result.endpoint),
        "ok": result.ok,
        "status": result.status,
        "error": result.error,
        "gone": result.gone,
    }


def short_endpoint(endpoint: str) -> str:
    return endpoint[:60] + "…" if len(endpoint) > 60 else endpoint


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
