"""Настройки сервиса: всё, что приходит из окружения, собрано в одном месте."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# корень проекта: src/configs/settings.py -> src/configs -> src -> .
BASE_DIR = Path(__file__).resolve().parents[2]
STATIC_DIR = BASE_DIR / "static"


@dataclass(frozen=True)
class Settings:
    database_url: str

    # Сколько картинок отдаём за один опрос
    page_size: int

    # Ключи Web Push. Без них подписка и рассылка просто выключены,
    # остальная часть сервиса работает как раньше.
    vapid_public_key: str
    vapid_private_key: str
    vapid_subject: str

    # Как часто фоновая задача сверяет список неотвеченных картинок
    watch_interval_sec: float
    # Потолок на размер сверяемого списка: id берём порциями, чтобы опрос
    # раз в несколько секунд не тащил из базы неограниченную выборку
    watch_limit: int

    base_dir: Path = BASE_DIR
    static_dir: Path = STATIC_DIR

    @property
    def push_enabled(self) -> bool:
        return bool(self.vapid_public_key and self.vapid_private_key)


def load_settings() -> Settings:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Не задана переменная окружения DATABASE_URL")

    return Settings(
        database_url=database_url,
        page_size=int(os.getenv("PAGE_SIZE", "30")),
        vapid_public_key=os.getenv("VAPID_PUBLIC_KEY", "").strip(),
        vapid_private_key=os.getenv("VAPID_PRIVATE_KEY", "").strip(),
        vapid_subject=os.getenv("VAPID_SUBJECT", "mailto:admin@example.com").strip(),
        watch_interval_sec=float(os.getenv("WATCH_INTERVAL_SEC", "10")),
        watch_limit=int(os.getenv("WATCH_LIMIT", "1000")),
    )


settings = load_settings()
