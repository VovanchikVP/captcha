"""Генерация пары VAPID-ключей для Web Push.

    uv run python gen_vapid_keys.py

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


def main() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    private_raw = key.private_numbers().private_value.to_bytes(32, "big")
    public_raw = key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    print(f"VAPID_PRIVATE_KEY={b64url(private_raw)}")
    print(f"VAPID_PUBLIC_KEY={b64url(public_raw)}")


if __name__ == "__main__":
    main()
