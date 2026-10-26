"""Сервис разметки капчи: показывает картинки без ответа и сохраняет введённые ответы."""

import os
import base64
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, Field

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("Не задана переменная окружения DATABASE_URL")

# Сколько картинок отдаём за один опрос
PAGE_SIZE = int(os.getenv("PAGE_SIZE", "30"))

BASE_DIR = Path(__file__).parent


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
    try:
        yield
    finally:
        await pool.close()


app = FastAPI(title="show-img", lifespan=lifespan)


class AnswerIn(BaseModel):
    answer: str = Field(min_length=1, max_length=255)


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


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
