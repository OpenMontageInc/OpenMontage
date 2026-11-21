"""URL policy condivisa per i provider video.

HTTPS per gli URL remoti; HTTP solo per i server locali di fiducia (loopback),
che non espongono traffico di rete e non possono usare TLS con certificato valido.
"""

from __future__ import annotations

from urllib.parse import urlsplit

_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})


def is_secure_remote_url(value: str) -> bool:
    """True per URL https con host, o http verso loopback. False per tutto il resto."""
    parsed = urlsplit(str(value).strip())
    if parsed.scheme == "https":
        return bool(parsed.netloc)
    if parsed.scheme == "http":
        return (parsed.hostname or "").lower() in _LOOPBACK
    return False
