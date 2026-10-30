"""Ручки разметки капчи."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from psycopg_pool import AsyncConnectionPool

from src.api.deps import get_pool
from src.models import AnswerIn
from src.services import images

router = APIRouter(prefix="/api/images", tags=["images"])

Pool = Annotated[AsyncConnectionPool, Depends(get_pool)]


@router.get("")
async def list_images(pool: Pool):
    """Все картинки, у которых ещё нет ответа."""
    return await images.list_unanswered(pool)


@router.post("/{image_id}/answer")
async def set_answer(image_id: str, payload: AnswerIn, pool: Pool):
    """Записывает ответ, после чего картинка перестаёт попадать в выдачу."""
    answer = payload.answer.strip()
    if not answer:
        raise HTTPException(status_code=422, detail="Пустой ответ")

    if not await images.set_answer(pool, image_id, answer):
        raise HTTPException(status_code=404, detail="Картинка не найдена")

    return {"id": image_id, "answer": answer}
