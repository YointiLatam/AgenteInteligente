import hashlib
import hmac

PREFIX = "sha256="


def sign(app_secret: str, body: bytes) -> str:
    digest = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return PREFIX + digest


def verify_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    """Check `X-Hub-Signature-256` against the HMAC-SHA256 of the raw body."""
    if not header or not header.startswith(PREFIX):
        return False
    return hmac.compare_digest(sign(app_secret, body), header)
