"""Генерация пары VAPID-ключей для Web Push.

    uv run python -m src.services.vapid

Приватный ключ кладётся в VAPID_PRIVATE_KEY (секрет, в git не коммитить),
публичный — в VAPID_PUBLIC_KEY, его браузер получает через /api/push/public-key.
Менять ключи после того, как кто-то подписался, нельзя: старые подписки
станут недействительными, их придётся оформить заново.
"""

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64url(raw: bytes) -> str:
    """base64url без выравнивающих '=' — формат, который ждут браузер и py-vapid."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def generate_keys() -> tuple[str, str]:
    """Возвращает пару (приватный ключ, публичный ключ) в base64url."""
    key = ec.generate_private_key(ec.SECP256R1())
    private_raw = key.private_numbers().private_value.to_bytes(32, "big")
    public_raw = key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return b64url(private_raw), b64url(public_raw)


def main() -> None:
    private_key, public_key = generate_keys()
    print(f"VAPID_PRIVATE_KEY={private_key}")
    print(f"VAPID_PUBLIC_KEY={public_key}")


if __name__ == "__main__":
    main()
