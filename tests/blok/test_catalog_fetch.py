"""Pulling a published catalog for an author's tooling: safely, and kept for the next time."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Self

import pytest

from arkitekt_spec.blok.catalogs import (
    ORKESTRATOR_CATALOG_ID,
    blok_view,
    forget_catalogs,
    known_catalog,
)
from arkitekt_spec.blok.catalogs import fetch as fetching
from arkitekt_spec.blok.catalogs.fetch import (
    CatalogUnavailable,
    cache_file,
    cached_catalog,
    fetch_catalog,
    resolve_catalog,
)

LAB = "https://acme.example/catalogs/lab/v1"
BENCH = "https://acme.example/catalogs/bench/v1"


def _catalog(identifier: str, name: str, extends: str, component: str) -> str:
    return json.dumps(
        {
            "id": identifier,
            "name": name,
            "version": 1,
            "extends": extends,
            "components": [{"name": component, "props": []}],
        }
    )


SERVED = {
    LAB: _catalog(LAB, "lab", ORKESTRATOR_CATALOG_ID, "Spectrum"),
    BENCH: _catalog(BENCH, "bench", LAB, "Bench"),
}


class _Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self, amount: int) -> bytes:
        return self.body[:amount]


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    forget_catalogs()
    yield
    forget_catalogs()


@pytest.fixture()
def web(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """What the web serves, by address; every pull is counted under ``"pulls"``."""
    served: dict[str, str] = dict(SERVED)
    pulls: list[str] = []

    class _Opener:
        def open(self, request: object, timeout: float) -> _Response:
            url = request.full_url  # type: ignore[attr-defined]
            pulls.append(url)
            assert request.get_header("Authorization") is None  # type: ignore[attr-defined]
            if url not in served:
                raise OSError("no route to host")
            return _Response(served[url].encode())

    monkeypatch.setattr(fetching.urllib.request, "build_opener", lambda *_: _Opener())
    served["pulls"] = pulls  # type: ignore[assignment]
    return served


def test_a_catalog_is_pulled_once_then_kept(
    web: dict[str, str], tmp_path: Path
) -> None:
    catalog = resolve_catalog(LAB, directory=tmp_path)

    assert catalog.name == "lab" and known_catalog(LAB) is catalog
    assert blok_view(LAB).component("Spectrum") is not None
    assert cache_file(LAB, tmp_path).exists() and web["pulls"] == [LAB]

    forget_catalogs()
    resolve_catalog(LAB, directory=tmp_path)
    assert web["pulls"] == [LAB], "the kept copy is used"


def test_what_it_extends_is_pulled_with_it(web: dict[str, str], tmp_path: Path) -> None:
    resolve_catalog(BENCH, directory=tmp_path)

    assert web["pulls"] == [BENCH, LAB]
    view = blok_view(BENCH)
    assert all(
        view.component(name) is not None
        for name in ("Bench", "Spectrum", "MikroScene", "Button")
    )


def test_offline_the_kept_copy_serves_a_refresh(
    web: dict[str, str], tmp_path: Path
) -> None:
    resolve_catalog(LAB, directory=tmp_path)
    forget_catalogs()
    del web[LAB]

    assert resolve_catalog(LAB, directory=tmp_path, refresh=True).name == "lab"


def test_nothing_is_pulled_when_told_not_to(
    web: dict[str, str], tmp_path: Path
) -> None:
    with pytest.raises(CatalogUnavailable, match="not kept here"):
        resolve_catalog(LAB, directory=tmp_path, fetch=False)
    assert web["pulls"] == []


def test_a_shipped_catalog_is_never_pulled(web: dict[str, str], tmp_path: Path) -> None:
    assert (
        resolve_catalog(ORKESTRATOR_CATALOG_ID, directory=tmp_path).name
        == "orkestrator"
    )
    assert web["pulls"] == []


@pytest.mark.parametrize(
    "address",
    [
        "http://acme.example/catalogs/lab/v1",
        "file:///etc/passwd",
        "https://acme.example/catalogs/lab",
        "lab",
    ],
)
def test_only_a_catalog_address_is_pulled(web: dict[str, str], address: str) -> None:
    with pytest.raises(CatalogUnavailable, match="not the address of a catalog"):
        fetch_catalog(address)
    assert web["pulls"] == []


def test_what_answers_must_be_the_catalog_asked_for(
    web: dict[str, str], tmp_path: Path
) -> None:
    web[LAB] = SERVED[BENCH]
    with pytest.raises(
        CatalogUnavailable, match="not 'https://acme.example/catalogs/lab/v1'"
    ):
        resolve_catalog(LAB, directory=tmp_path)
    assert not cache_file(LAB, tmp_path).exists() and known_catalog(LAB) is None


def test_an_answer_that_is_no_catalog_or_too_large_is_refused(
    web: dict[str, str],
) -> None:
    web[LAB] = "<html>not found</html>"
    with pytest.raises(CatalogUnavailable, match="is no catalog"):
        fetch_catalog(LAB)
    web[LAB] = SERVED[LAB]
    with pytest.raises(CatalogUnavailable, match="larger than 10 bytes"):
        fetch_catalog(LAB, max_bytes=10)


def test_a_pulled_catalog_may_only_add(web: dict[str, str], tmp_path: Path) -> None:
    web[LAB] = _catalog(LAB, "lab", ORKESTRATOR_CATALOG_ID, "Button")
    with pytest.raises(CatalogUnavailable, match="redefines <Button>"):
        resolve_catalog(LAB, directory=tmp_path)
    assert known_catalog(LAB) is None


def test_a_kept_file_that_is_not_the_catalog_is_ignored(tmp_path: Path) -> None:
    cache_file(LAB, tmp_path).write_text(SERVED[BENCH])

    assert cached_catalog(LAB, tmp_path) is None


def test_a_redirect_off_https_is_refused() -> None:
    import urllib.error
    import urllib.request

    handler = fetching._HttpsOnly()  # pyright: ignore[reportPrivateUsage]
    request = urllib.request.Request(LAB)
    with pytest.raises(urllib.error.HTTPError):
        handler.redirect_request(
            request,
            None,  # type: ignore[arg-type]
            302,
            "Found",
            {},  # type: ignore[arg-type]
            "http://169.254.169.254/latest",
        )
