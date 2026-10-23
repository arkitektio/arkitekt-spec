"""TLS to a hub that registered its certificate with the coordination server.

Such a hub serves ONE self-signed certificate for every host it is reached at, and the
coordination server — reached over a publicly valid certificate — hands it to apps on each of
the hub's instances (``tls.certificates`` in its fakts). The fakts client sets it on the TLS
aliases it resolves of such an instance (``Alias.certificates``), and:

- Python clients (aiohttp, websockets, httpx) verify those aliases by exactly that
  certificate and nothing else: :func:`pinned_context`, via ``Alias.ssl_context()``.
- Any other client is the service's to configure, as its implementation allows; these are
  the tools for it:

  - :func:`bundle_file` — a file of the public roots plus the certificates, for a client that
    takes a path (duckdb's ``ca_cert_file``). Changes nothing else.
  - :func:`trust_natively` — points ``SSL_CERT_FILE`` at such a file, for a client that only
    reads its roots from there (obstore, LiveKit: rustls). Process-wide: OpenSSL's default
    paths, httpx and child processes read it too, so call it only where that client is built.

  Both make the certificate an extra root beside the public ones: weaker than pinning (a
  public CA could vouch for the hub's names too), but what those clients offer.
"""

from __future__ import annotations

import hashlib
import os
import ssl
import tempfile
import threading
from pathlib import Path

#: Hub certificates this process has been told to trust natively, by their PEM.
_native: dict[str, None] = {}
_native_lock = threading.Lock()
#: What ``SSL_CERT_FILE`` named before this module first pointed it elsewhere.
_base_bundle: str | None = None


def public_roots() -> str | None:
    """The file the public roots come from: certifi's bundle where certifi is installed (as it
    is beside every Arkitekt client), else ``None`` (the system's own store)."""
    try:
        import certifi
    except ImportError:  # pragma: no cover - certifi ships with every client
        return None
    return certifi.where()


def default_context() -> ssl.SSLContext:
    """The context an unpinned alias is reached with: the public roots, as before."""
    return ssl.create_default_context(cafile=public_roots())


def pinned_context(certificates: list[str]) -> ssl.SSLContext:
    """A context that trusts exactly ``certificates`` — no public roots — and checks the host
    name against them, as usual."""
    return ssl.create_default_context(cadata="\n".join(certificates))


def bundle_file(certificates: list[str]) -> str:
    """A file of the public roots — or what ``SSL_CERT_FILE`` named before :func:`trust_natively`
    first changed it — plus every certificate passed here or there so far (so a second hub keeps
    the first). Named by its contents, written once; nothing else changes."""
    global _base_bundle
    with _native_lock:
        if _base_bundle is None:
            _base_bundle = os.environ.get("SSL_CERT_FILE") or public_roots() or ""
        for pem in certificates:
            _native.setdefault(pem.strip(), None)
        base = (
            Path(_base_bundle).read_text() if _base_bundle and Path(_base_bundle).is_file() else ""
        )
        bundle = base.rstrip() + "\n" + "\n".join(_native) + "\n"
        digest = hashlib.sha256(bundle.encode()).hexdigest()[:16]
        path = Path(tempfile.gettempdir()) / f"arkitekt-hub-roots-{digest}.pem"
        if not path.exists():
            partial = path.with_suffix(f".{os.getpid()}.part")
            partial.write_text(bundle)
            partial.replace(path)
        return str(path)


def trust_natively(certificates: list[str]) -> str:
    """Point ``SSL_CERT_FILE`` at :func:`bundle_file`, for clients that read their roots only from
    there; returns the path. Process-wide — see the module's notes — so a service calls it right
    before it builds such a client, and only then."""
    path = bundle_file(certificates)
    os.environ["SSL_CERT_FILE"] = path
    return path
