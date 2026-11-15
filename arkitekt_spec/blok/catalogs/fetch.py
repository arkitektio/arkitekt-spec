"""Resolve a published catalog for an author's tooling: from what ships, from a cache, or by fetching it.

``arkitekt check`` and the language server hold a blok to the catalog it is written for. One
that ships with the package needs nothing; any other is the frontend's to publish at its id,
and is pulled from there. Nothing else pulls: an app registering its bloks never touches the
network, and a server holds what a frontend registered with it.

Pulling is kept safe:

- The id is an ``https`` address (``vocabulary.json``: ``catalogs.id``); a redirect anywhere
  but to ``https`` is refused.
- At most :data:`MAX_BYTES` are read, within :data:`TIMEOUT` seconds, with no credentials and
  nothing that identifies the author.
- The answer is data: parsed as JSON and checked field by field
  (:func:`~arkitekt_spec.blok.catalogs.parse_catalog`), never executed. It must say the id it
  was asked for, and may only add to the catalog it extends.
- What was pulled is kept, so the next check works offline.
"""

import hashlib
import os
import urllib.error
import urllib.request
from pathlib import Path

from arkitekt_spec.blok.catalogs import known_catalog, parse_catalog, register_catalog
from arkitekt_spec.blok.vocabulary import load_vocabulary
from arkitekt_spec.declare.catalogs import Catalog

MAX_BYTES = 2_000_000
"""The largest catalog pulled: the base catalog, the largest there is, is a fifth of it."""
TIMEOUT = 10.0
"""Seconds a pull may take."""
MAX_DEPTH = 8
"""How many catalogs a chain of ``extends`` may pull."""


class CatalogUnavailable(ValueError):
    """A catalog could not be resolved: not cached and not reachable, or what answered was not it."""


class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]  # noqa: ANN001, ANN202
        if not newurl.startswith("https://"):
            raise urllib.error.HTTPError(newurl, code, "a catalog is only pulled over https", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def cache_directory() -> Path:
    """Where pulled catalogs are kept: ``$ARKITEKT_CATALOG_CACHE``, else the user's cache directory."""
    override = os.environ.get("ARKITEKT_CATALOG_CACHE")
    if override:
        return Path(override)
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "arkitekt" / "catalogs"


def cache_file(identifier: str, directory: Path | None = None) -> Path:
    """The file a catalog is kept in: named by the hash of its id, so no id can name a path."""
    return (directory or cache_directory()) / f"{hashlib.sha256(identifier.encode()).hexdigest()}.json"


def _checked(text: str, identifier: str, where: str) -> Catalog:
    catalog = parse_catalog(text, where)
    if catalog.id != identifier:
        raise CatalogUnavailable(f"{where} is the catalog {catalog.id!r}, not {identifier!r}")
    return catalog


def cached_catalog(identifier: str, directory: Path | None = None) -> Catalog | None:
    """The catalog as it was last pulled, or None when it never was (or what is kept is not it)."""
    file = cache_file(identifier, directory)
    try:
        return _checked(file.read_text(encoding="utf-8"), identifier, str(file))
    except (OSError, ValueError):
        return None


def fetch_catalog(identifier: str, *, timeout: float = TIMEOUT, max_bytes: int = MAX_BYTES) -> tuple[Catalog, str]:
    """Pull the catalog published at ``identifier``: the catalog, and its text as it was served.

    Raises:
        CatalogUnavailable: If the id is no catalog address, nothing answers in time, the
            answer is larger than ``max_bytes``, or it is not that catalog.
    """
    if not load_vocabulary().catalog_id.fullmatch(identifier):
        raise CatalogUnavailable(f"{identifier!r} is not the address of a catalog")
    request = urllib.request.Request(identifier, headers={"Accept": "application/json", "User-Agent": "arkitekt-spec"})
    opener = urllib.request.build_opener(_HttpsOnly)
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(max_bytes + 1)
    except (OSError, ValueError) as error:
        raise CatalogUnavailable(f"the catalog {identifier!r} could not be pulled: {error}") from error
    if len(body) > max_bytes:
        raise CatalogUnavailable(f"the catalog {identifier!r} is larger than {max_bytes} bytes")
    try:
        text = body.decode("utf-8")
        return _checked(text, identifier, f"what {identifier} serves"), text
    except CatalogUnavailable:
        raise
    except ValueError as error:
        raise CatalogUnavailable(f"what {identifier} serves is no catalog: {error}") from error


def resolve_catalog(
    identifier: str,
    *,
    fetch: bool = True,
    refresh: bool = False,
    directory: Path | None = None,
    timeout: float = TIMEOUT,
    _depth: int = 0,
) -> Catalog:
    """The catalog called ``identifier``, known here afterwards (and every catalog it extends).

    One that ships or was registered already is returned as it is. Else the kept copy is used,
    and it is pulled only when there is none; with ``refresh`` it is pulled first, and the
    kept copy is what is left when it cannot be (offline). ``fetch=False`` never pulls.

    Raises:
        CatalogUnavailable: If it is neither kept nor reachable, or its chain is broken.
    """
    known = known_catalog(identifier)
    if known is not None:
        return known
    if _depth >= MAX_DEPTH:
        raise CatalogUnavailable(f"the catalog {identifier!r} is more than {MAX_DEPTH} catalogs away from the base")
    catalog = None if (refresh and fetch) else cached_catalog(identifier, directory)
    if catalog is None and fetch:
        try:
            catalog, text = fetch_catalog(identifier, timeout=timeout)
        except CatalogUnavailable:
            catalog = cached_catalog(identifier, directory)
            if catalog is None:
                raise
        else:
            file = cache_file(identifier, directory)
            try:
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(text, encoding="utf-8")
            except OSError:
                pass  # a cache that cannot be written is no reason to fail the check
    if catalog is None:
        raise CatalogUnavailable(f"the catalog {identifier!r} is not kept here, and was not pulled")
    if catalog.extends is not None:
        resolve_catalog(catalog.extends, fetch=fetch, refresh=refresh, directory=directory, timeout=timeout, _depth=_depth + 1)
    try:
        register_catalog(catalog)
    except ValueError as error:
        raise CatalogUnavailable(str(error)) from error
    return catalog


__all__ = [
    "MAX_BYTES",
    "MAX_DEPTH",
    "TIMEOUT",
    "CatalogUnavailable",
    "cache_directory",
    "cache_file",
    "cached_catalog",
    "fetch_catalog",
    "resolve_catalog",
]
