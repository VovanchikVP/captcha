"""Сервис разметки капчи: показывает картинки без ответа и сохраняет введённые ответы."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.api import api_router
from src.configs import settings
from src.services import database
from src.services.push import watch_new_images

log = logging.getLogger("show-img")


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = database.create_pool()
    # wait=False: не блокируем старт, если база ещё недоступна — иначе процесс
    # не начинает слушать порт и пробы получают connection refused вместо 503.
    await pool.open(wait=False)
    app.state.pool = pool

    watcher = None
    if settings.push_enabled:
        watcher = asyncio.create_task(watch_new_images(pool))
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


def create_app() -> FastAPI:
    app = FastAPI(title="show-img", lifespan=lifespan)
    app.include_router(api_router)
    app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")
    return app


app = create_app()
