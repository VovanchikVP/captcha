"""Сервис разметки капчи: показывает картинки без ответа и сохраняет введённые ответы."""

import os
import asyncio
import base64
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, Field
from pywebpush import WebPushException, webpush_async

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("Не задана переменная окружения DATABASE_URL")

# Сколько картинок отдаём за один опрос
PAGE_SIZE = int(os.getenv("PAGE_SIZE", "30"))

# Ключи Web Push. Без них подписка и рассылка просто выключены,
# остальная часть сервиса работает как раньше.
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "").strip()
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "").strip()
VAPID_SUBJECT = os.getenv("VAPID_SUBJECT", "mailto:admin@example.com").strip()
PUSH_ENABLED = bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY)

# Как часто фоновая задача сверяет список неотвеченных картинок
WATCH_INTERVAL_SEC = float(os.getenv("WATCH_INTERVAL_SEC", "10"))
# Потолок на размер сверяемого списка: id берём порциями, чтобы опрос
# раз в несколько секунд не тащил из базы неограниченную выборку
WATCH_LIMIT = int(os.getenv("WATCH_LIMIT", "1000"))

BASE_DIR = Path(__file__).parent

log = logging.getLogger("show-img")


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = AsyncConnectionPool(
        DATABASE_URL,
        min_size=1,
        max_size=5,
        kwargs={"connect_timeout": 5},
        open=False,
    )
    # wait=False: не блокируем старт, если база ещё недоступна — иначе процесс
    # не начинает слушать порт и пробы получают connection refused вместо 503.
    await pool.open(wait=False)
    app.state.pool = pool

    watcher = None
    if PUSH_ENABLED:
        watcher = asyncio.create_task(watch_new_images(app))
    else:
        log.warning("VAPID-ключи не заданы — уведомления выключены")

    try:
        yield
    finally:
        if watcher:
            watcher.cancel()
            # дожидаемся отмены, иначе задача умрёт уже после закрытия пула
            await asyncio.gather(watcher, return_exceptions=True)
        await pool.close()


app = FastAPI(title="show-img", lifespan=lifespan)


class AnswerIn(BaseModel):
    answer: str = Field(min_length=1, max_length=255)


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=1, max_length=255)
    auth: str = Field(min_length=1, max_length=255)


class SubscriptionIn(BaseModel):
    """То, что отдаёт PushSubscription.toJSON() в браузере."""

    endpoint: str = Field(min_length=1, max_length=2048)
    keys: PushKeys


class EndpointIn(BaseModel):
    endpoint: str = Field(min_length=1, max_length=2048)


@app.get("/api/images")
async def list_images():
    """Все картинки, у которых ещё нет ответа."""
    async with app.state.pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT id, image
                FROM captcha
                WHERE answer IS NULL OR answer = ''
                ORDER BY id
                LIMIT %s
                """,
                (PAGE_SIZE,),
            )
            rows = await cur.fetchall()

    return [{"id": str(row["id"]), "image": to_src(row["image"])} for row in rows]


@app.post("/api/images/{image_id}/answer")
async def set_answer(image_id: str, payload: AnswerIn):
    """Записывает ответ, после чего картинка перестаёт попадать в выдачу."""
    answer = payload.answer.strip()
    if not answer:
        raise HTTPException(status_code=422, detail="Пустой ответ")

    async with app.state.pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE captcha SET answer = %s WHERE id = %s",
                (answer, image_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Картинка не найдена")

    return {"id": image_id, "answer": answer}


def to_src(image: str | bytes | None) -> str:
    """Приводит содержимое поля image к значению, пригодному для <img src>."""
    if image is None:
        return ""
    if isinstance(image, (bytes, bytearray, memoryview)):
        return "data:image/png;base64," + base64.b64encode(bytes(image)).decode()

    value = image.strip()
    if value.startswith(("data:", "http://", "https://", "/")):
        return value
    # иначе считаем, что в базе лежит голый base64
    return value


# --- Web Push ---------------------------------------------------------------

CREATE_SUBSCRIPTIONS_SQL = """
CREATE TABLE IF NOT EXISTS push_subscription (
    endpoint   text PRIMARY KEY,
    p256dh     text NOT NULL,
    auth       text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
)
"""


@app.get("/api/push/public-key")
async def push_public_key():
    """Публичный VAPID-ключ для PushManager.subscribe(). Пустой — значит push выключен."""
    return {"key": VAPID_PUBLIC_KEY if PUSH_ENABLED else ""}


@app.post("/api/push/subscribe")
async def push_subscribe(sub: SubscriptionIn):
    if not PUSH_ENABLED:
        raise HTTPException(status_code=503, detail="Уведомления не настроены")

    async with app.state.pool.connection() as conn:
        async with conn.cursor() as cur:
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
                (sub.endpoint, sub.keys.p256dh, sub.keys.auth),
            )
    return {"status": "ok"}


@app.post("/api/push/unsubscribe")
async def push_unsubscribe(payload: EndpointIn):
    async with app.state.pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(CREATE_SUBSCRIPTIONS_SQL)
            await cur.execute(
                "DELETE FROM push_subscription WHERE endpoint = %s", (payload.endpoint,)
            )
    return {"status": "ok"}


async def fetch_unanswered_ids(app: FastAPI) -> set[str]:
    async with app.state.pool.connection(timeout=5) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id
                FROM captcha
                WHERE answer IS NULL OR answer = ''
                ORDER BY id
                LIMIT %s
                """,
                (WATCH_LIMIT,),
            )
            return {str(row[0]) for row in await cur.fetchall()}


async def watch_new_images(app: FastAPI):
    """Следит за появлением новых неотвеченных картинок и шлёт push.

    В таблице нет ни счётчика, ни времени создания (id — uuid), поэтому
    «новое» считаем диффом множества id между опросами.
    """
    known: set[str] | None = None

    while True:
        await asyncio.sleep(WATCH_INTERVAL_SEC)
        try:
            current = await fetch_unanswered_ids(app)
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
            await send_push(app, len(added))


async def send_push(app: FastAPI, count: int):
    """Рассылает уведомление всем подписчикам, попутно убирая мёртвые подписки."""
    try:
        async with app.state.pool.connection(timeout=5) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
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

    dead = [row["endpoint"] for row, ok in zip(subscriptions, results) if ok is False]
    if dead:
        await drop_subscriptions(app, dead)


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
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={"sub": VAPID_SUBJECT},
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


async def drop_subscriptions(app: FastAPI, endpoints: list[str]):
    try:
        async with app.state.pool.connection(timeout=5) as conn:
            async with conn.cursor() as cur:
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


@app.get("/healthz")
async def healthz():
    """Liveness: процесс жив, в базу не ходим."""
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    """Readiness: проверяем, что пул действительно достаёт соединение до базы."""
    try:
        # ждём соединение не дольше пробы, иначе readinessProbe отвалится
        # по таймауту и причина не попадёт ни в логи, ни в events
        async with app.state.pool.connection(timeout=2) as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"База недоступна: {exc}") from exc
    return {"status": "ok"}


@app.get("/")
async def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/sw.js")
async def service_worker():
    """Отдаём с корня: со /static/ у воркера была бы область видимости /static/."""
    return FileResponse(
        BASE_DIR / "static" / "sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/manifest.webmanifest")
async def manifest():
    return FileResponse(
        BASE_DIR / "static" / "manifest.webmanifest",
        media_type="application/manifest+json",
    )


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
