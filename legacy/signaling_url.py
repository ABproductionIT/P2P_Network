"""Normalize and validate signaling WebSocket URLs.

Bare signaling_server.py speaks plain ws:// (no TLS). Clients must not use
0.0.0.0 (bind-only) or assume wss:// without a TLS terminator.
"""
from __future__ import annotations

from urllib.parse import urlparse, urlunparse


BIND_ONLY_HOSTS = frozenset({"0.0.0.0", "::", "[::]"})


class SignalingUrlError(ValueError):
    """Invalid or unusable signaling URL."""


def _host_of(url: str) -> str | None:
    try:
        return urlparse(url).hostname
    except Exception:
        return None


def normalize_signaling_url(raw: str) -> str:
    """Return a ws:// or wss:// URL suitable for websockets.connect.

    Rules:
    - Empty → error
    - http(s):// → ws(s)://
    - Bare host[:port][/path] → ws://… (no TLS by default)
    - 0.0.0.0 / :: → error (bind address, not a connect target)
    """
    value = (raw or "").strip()
    if not value:
        raise SignalingUrlError(
            "empty signaling URL — use ws://127.0.0.1:9000 or ws://<LAN-IP>:9000"
        )

    lower = value.lower()
    if lower.startswith("https://"):
        value = "wss://" + value[8:]
    elif lower.startswith("http://"):
        value = "ws://" + value[7:]
    elif not (lower.startswith("ws://") or lower.startswith("wss://")):
        value = "ws://" + value

    value = value.rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in ("ws", "wss") or not parsed.hostname:
        raise SignalingUrlError(
            f"invalid signaling URL {raw!r} — expected ws://HOST:9000"
        )

    host = parsed.hostname
    if host in BIND_ONLY_HOSTS or host.lower() in BIND_ONLY_HOSTS:
        raise SignalingUrlError(
            f"{host} is a bind address, not a connect URL. "
            "Use ws://127.0.0.1:9000 (same machine) or ws://<your-LAN-IP>:9000 "
            "(e.g. ws://10.10.1.97:9000). Do not use wss:// unless TLS is in front."
        )

    # Rebuild to normalize (e.g. drop default junk)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path or "", "", parsed.query, ""))


def plain_ws_fallback(url: str) -> str | None:
    """If url is wss://, return the ws:// equivalent for retry against a bare server."""
    if url.lower().startswith("wss://"):
        return "ws://" + url[6:]
    return None


def hint_for_connect_failure(url: str, exc: BaseException) -> str:
    """Human-readable hint when websockets.connect fails."""
    err = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    lines = [f"cannot reach signaling at {url} ({err})"]
    if url.lower().startswith("wss://"):
        alt = plain_ws_fallback(url)
        lines.append(
            "HINT: bare signaling_server.py speaks plain ws:// (no TLS). "
            "wss:// only works behind nginx/Caddy (or similar)."
        )
        if alt:
            lines.append(f"HINT: try --signaling {alt}")
    host = _host_of(url)
    if host in BIND_ONLY_HOSTS:
        lines.append(
            "HINT: 0.0.0.0 is bind-only — use 127.0.0.1 or your LAN IP."
        )
    return "\n".join(lines)
