"""Сборка всех роутеров в один."""

from fastapi import APIRouter

from src.api import health, images, pages, push

api_router = APIRouter()
api_router.include_router(images.router)
api_router.include_router(push.router)
api_router.include_router(health.router)
api_router.include_router(pages.router)
