"""Пробы для Kubernetes."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from psycopg_pool import AsyncConnectionPool

from src.api.deps import get_pool
from src.services import database

router = APIRouter(tags=["health"])

Pool = Annotated[AsyncConnectionPool, Depends(get_pool)]


@router.get("/healthz")
async def healthz():
    """Liveness: процесс жив, в базу не ходим."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(pool: Pool):
    """Readiness: проверяем, что пул действительно достаёт соединение до базы."""
    try:
        await database.ping(pool)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"База недоступна: {exc}") from exc
    return {"status": "ok"}
