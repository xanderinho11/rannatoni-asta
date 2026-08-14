from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException, webpush

import db

_DATA_DIR = Path(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data")))
_PRIVATE_KEY_PATH = _DATA_DIR / "vapid_private.pem"
_PUBLIC_KEY_PATH = _DATA_DIR / "vapid_public.txt"
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:rannatoni@example.com")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def ensure_vapid_keys() -> str:
    """Genera le chiavi una sola volta nel volume persistente e restituisce la pubblica."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    if _PRIVATE_KEY_PATH.exists():
        private = serialization.load_pem_private_key(_PRIVATE_KEY_PATH.read_bytes(), password=None)
    else:
        private = ec.generate_private_key(ec.SECP256R1())
        _PRIVATE_KEY_PATH.write_bytes(
            private.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    numbers = private.public_key().public_numbers()
    raw_public = b"\x04" + numbers.x.to_bytes(32, "big") + numbers.y.to_bytes(32, "big")
    public = _b64url(raw_public)
    _PUBLIC_KEY_PATH.write_text(public, encoding="utf-8")
    return public


def public_key() -> str:
    if _PUBLIC_KEY_PATH.exists() and _PRIVATE_KEY_PATH.exists():
        value = _PUBLIC_KEY_PATH.read_text(encoding="utf-8").strip()
        if value:
            return value
    return ensure_vapid_keys()


def _send_one(subscription: dict, payload: dict):
    webpush(
        subscription_info=subscription,
        data=json.dumps(payload, ensure_ascii=False),
        vapid_private_key=str(_PRIVATE_KEY_PATH),
        vapid_claims={"sub": VAPID_SUBJECT},
        ttl=120,
        timeout=8,
    )


async def send_to_teams(teams: list[str] | set[str], *, title: str, body: str, url: str = "/auction", tag: str = "rannatoni"):
    recipients = db.push_subscriptions_for_teams(teams)
    if not recipients:
        return {"sent": 0, "removed": 0}
    payload = {"title": title, "body": body, "url": url, "tag": tag}
    sent = 0
    removed = 0
    for item in recipients:
        try:
            await asyncio.to_thread(_send_one, item["subscription"], payload)
            sent += 1
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                db.delete_push_subscription(item["subscription"]["endpoint"])
                removed += 1
            else:
                print(f"[push] invio non riuscito per {item['team']}: {exc}")
        except Exception as exc:
            print(f"[push] errore per {item['team']}: {exc}")
    return {"sent": sent, "removed": removed}
