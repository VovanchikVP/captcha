"""Пул соединений с базой."""

from psycopg_pool import AsyncConnectionPool

from src.configs import settings


def create_pool() -> AsyncConnectionPool:
    return AsyncConnectionPool(
        settings.database_url,
        min_size=1,
        max_size=5,
        kwargs={"connect_timeout": 5},
        open=False,
    )


async def ping(pool: AsyncConnectionPool) -> None:
    """Проверяет, что пул действительно достаёт соединение до базы."""
    # ждём соединение не дольше пробы, иначе readinessProbe отвалится
    # по таймауту и причина не попадёт ни в логи, ни в events
    async with pool.connection(timeout=2) as conn, conn.cursor() as cur:
        await cur.execute("SELECT 1")
