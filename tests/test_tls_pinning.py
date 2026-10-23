"""A pinned alias is verified by exactly its hub's certificate; an unpinned one as before."""

import os
import socket
import ssl
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from arkitekt_spec.declare import tls
from arkitekt_spec.declare.wiring import Alias

FIXTURES = Path(__file__).parent / "fixtures" / "tls"
HUB = (FIXTURES / "hub.crt").read_text()
OTHER = (FIXTURES / "other.crt").read_text()


def serve(name: str) -> Iterator[int]:
    """A TLS server on 127.0.0.1 presenting fixture ``name``, answering one byte per connection."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(FIXTURES / f"{name}.crt", FIXTURES / f"{name}.key")
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]

    def run() -> None:
        while True:
            try:
                raw, _ = listener.accept()
            except OSError:
                return
            try:
                with context.wrap_socket(raw, server_side=True) as conn:
                    conn.sendall(b"!")
            except (ssl.SSLError, OSError):
                pass

    threading.Thread(target=run, daemon=True).start()
    yield port
    listener.close()


@pytest.fixture
def hub_server() -> Iterator[int]:
    yield from serve("hub")


@pytest.fixture
def other_server() -> Iterator[int]:
    yield from serve("other")


def connect(context: ssl.SSLContext, port: int, host: str = "localhost") -> bytes:
    with (
        socket.create_connection(("127.0.0.1", port), timeout=5) as raw,
        context.wrap_socket(raw, server_hostname=host) as conn,
    ):
        return conn.recv(1)


def alias(**fields: object) -> Alias:
    return Alias(id="a", host="localhost", ssl=True, **fields)


def test_a_pinned_alias_trusts_its_hubs_certificate(hub_server: int) -> None:
    pinned = alias(certificates=[HUB])
    assert pinned.is_pinned()
    assert connect(pinned.ssl_context(), hub_server) == b"!"
    assert connect(pinned.ssl_context(), hub_server, host="127.0.0.1") == b"!"


def test_and_nothing_else(other_server: int) -> None:
    with pytest.raises(ssl.SSLCertVerificationError):
        connect(alias(certificates=[HUB]).ssl_context(), other_server)


def test_for_its_own_names_only(hub_server: int) -> None:
    with pytest.raises(ssl.SSLCertVerificationError):
        connect(alias(certificates=[HUB]).ssl_context(), hub_server, host="lab.local")


def test_an_unpinned_alias_is_verified_by_the_public_roots(hub_server: int) -> None:
    unpinned = alias()
    assert not unpinned.is_pinned()
    with pytest.raises(ssl.SSLCertVerificationError):
        connect(unpinned.ssl_context(), hub_server)


def test_plain_aliases_are_never_pinned() -> None:
    assert not Alias(id="p", host="localhost", ssl=False, certificates=[HUB]).is_pinned()


def test_the_certificates_are_never_sent_or_cached() -> None:
    assert "certificates" not in alias(certificates=[HUB]).model_dump()


def test_native_trust_keeps_the_public_roots_and_every_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setattr(tls, "_base_bundle", None)
    monkeypatch.setattr(tls, "_native", {})

    tls.trust_natively([HUB])
    first = Path(os.environ["SSL_CERT_FILE"]).read_text()
    assert HUB.strip() in first
    assert Path(tls.public_roots() or "").read_text().strip()[:200] in first

    path = tls.trust_natively([OTHER])
    both = Path(path).read_text()
    assert HUB.strip() in both and OTHER.strip() in both
    assert os.environ["SSL_CERT_FILE"] == path

    # A bundle Python itself can use.
    context = ssl.create_default_context(cafile=path)
    assert context.cert_store_stats()["x509_ca"] >= 1


def test_a_bundle_file_changes_nothing_else(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SSL_CERT_FILE", "/unchanged")
    monkeypatch.setattr(tls, "_base_bundle", None)
    monkeypatch.setattr(tls, "_native", {})
    path = tls.bundle_file([HUB])
    assert HUB.strip() in Path(path).read_text()
    assert os.environ["SSL_CERT_FILE"] == "/unchanged"


def test_the_alias_only_carries_the_certificates() -> None:
    """How a client trusts them is the service's: the alias has no native-trust method."""
    assert not hasattr(Alias, "trust_natively")
