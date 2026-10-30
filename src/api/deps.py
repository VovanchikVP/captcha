"""Общие зависимости роутеров."""

from fastapi import Request
from psycopg_pool import AsyncConnectionPool


def get_pool(request: Request) -> AsyncConnectionPool:
    return request.app.state.pool
