"""Статика, которую нужно отдавать с корня."""

from fastapi import APIRouter
from fastapi.responses import FileResponse

from src.configs import settings

router = APIRouter(include_in_schema=False)


@router.get("/")
async def index():
    return FileResponse(settings.static_dir / "index.html")


@router.get("/sw.js")
async def service_worker():
    """Отдаём с корня: со /static/ у воркера была бы область видимости /static/."""
    return FileResponse(
        settings.static_dir / "sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/manifest.webmanifest")
async def manifest():
    return FileResponse(
        settings.static_dir / "manifest.webmanifest",
        media_type="application/manifest+json",
    )
