"""Работа с таблицей captcha."""

import base64

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from src.configs import settings

SELECT_UNANSWERED_SQL = """
SELECT {columns}
FROM captcha
WHERE answer IS NULL OR answer = ''
ORDER BY id
LIMIT %s
"""


async def list_unanswered(pool: AsyncConnectionPool) -> list[dict]:
    """Все картинки, у которых ещё нет ответа."""
    async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            SELECT_UNANSWERED_SQL.format(columns="id, image"),
            (settings.page_size,),
        )
        rows = await cur.fetchall()

    return [{"id": str(row["id"]), "image": to_src(row["image"])} for row in rows]


async def fetch_unanswered_ids(pool: AsyncConnectionPool) -> set[str]:
    async with pool.connection(timeout=5) as conn, conn.cursor() as cur:
        await cur.execute(
            SELECT_UNANSWERED_SQL.format(columns="id"),
            (settings.watch_limit,),
        )
        return {str(row[0]) for row in await cur.fetchall()}


async def set_answer(pool: AsyncConnectionPool, image_id: str, answer: str) -> bool:
    """Записывает ответ, после чего картинка перестаёт попадать в выдачу.

    False — картинки с таким id нет.
    """
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "UPDATE captcha SET answer = %s WHERE id = %s",
            (answer, image_id),
        )
        return cur.rowcount > 0


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
