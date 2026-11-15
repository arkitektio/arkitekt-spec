"""The catalogs a blok is drawn with: the base catalog, and the app catalogs that extend it.

A *catalog* is a versioned set of components, named by the address it is published at (its
``id``). There are two sorts:

- The **base catalog** (``catalogs/base@3.json``, ``https://arkitekt.live/catalogs/base/v3``):
  everything every renderer draws. The language's own elements (``if``, ``foreach``,
  ``suspense``, the context roots), the html components, the structure and run parts a host
  supplies (``args``, ``ask``, ``timeline``), the scene and the logo shapes. A blok written for
  it runs everywhere. The spec owns it.
- An **app catalog** extends another (in the end the base) with what one frontend draws beyond
  it. ``catalogs/orkestrator@1.json`` is a snapshot of the Orkestrator's
  (``https://arkitekt.live/catalogs/orkestrator/v1``: mikro's scene and tables), shipped so a
  blok written for it can be checked offline. Any other is the frontend's to publish: tooling
  hands it over with :func:`register_catalog` (see :mod:`.fetch`).

A blok names the one catalog it is written for; the base when it names none. A version only
ever gains components and props, each saying the minor that added it (``since``), so
:func:`minor_needed` tells how new a renderer has to be.

Catalog rules are not the language's. A server holds a blok to the language alone
(:func:`~arkitekt_spec.declare.blok.validate.check_blok_tree` without a view); what a catalog
says is checked by whoever draws it, and by an author's tooling when it asks
(:func:`checking_catalogs`).
"""

import contextlib
import contextvars
import functools
import json
from collections.abc import Generator, Mapping
from dataclasses import dataclass, field
from importlib import resources

from arkitekt_spec.blok.vocabulary import JSON, Function, Param, load_vocabulary
from arkitekt_spec.declare.catalogs import Catalog, ComponentSpec, parse_catalog_components

BASE_NAME = "base"
LANGUAGE_GROUP = "language"
"""The family of the language's own elements (control flow, async boundaries, the context roots)."""
HTML_GROUP = "html"
"""The family of the plain html elements (``div``, ``p``, ``span``): loose text inside them stays html."""
ORKESTRATOR_CATALOG_ID = "https://arkitekt.live/catalogs/orkestrator/v1"
"""The Orkestrator's catalog: what it draws beyond the base."""

_registered: dict[str, Catalog] = {}
"""Catalogs tooling handed over (:func:`register_catalog`), by id."""


def _text(file: str) -> str:
    """A file under ``arkitekt_spec/blok``: ``catalogs/base@3.json``."""
    return resources.files("arkitekt_spec.blok").joinpath(file).read_text(encoding="utf-8")


def label_of(catalog: Catalog) -> str:
    """A catalog as a message names it: ``base@3``, or its bare name when it has no version."""
    return f"{catalog.name}@{catalog.version}" if catalog.version is not None else catalog.name


def parse_catalog(raw: "str | Mapping[str, JSON]", where: str = "the catalog") -> Catalog:
    """A catalog file (``{id, name, version, minor, extends, components}``) as a :class:`Catalog`.

    Only data is read: nothing of the payload is ever executed, and every field is checked.

    Raises:
        ValueError: If the payload is no catalog: not an object, no name, a version or minor
            that is not an integer, an id that is not a catalog address, or a malformed component.
    """
    vocabulary = load_vocabulary()
    payload: JSON = json.loads(raw) if isinstance(raw, str) else dict(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"{where} is not a catalog")
    name = payload.get("name")
    version = payload.get("version")
    minor = payload.get("minor", 0)
    identifier = payload.get("id")
    extends = payload.get("extends")
    description = payload.get("description")
    if not isinstance(name, str) or not vocabulary.catalog_name.fullmatch(name):
        raise ValueError(f"{where} has no name")
    for what, number in (("version", version), ("minor", minor)):
        if number is not None and (not isinstance(number, int) or isinstance(number, bool) or number < 0):
            raise ValueError(f"{where} has a {what} that is not an integer")
    for what, address in (("id", identifier), ("extends", extends)):
        if address is not None and (not isinstance(address, str) or not vocabulary.catalog_id.fullmatch(address)):
            raise ValueError(f"{where} has an {what} that is not the address of a catalog")
    return Catalog(
        name=name,
        version=version if isinstance(version, int) else None,
        components=tuple(parse_catalog_components(payload, camel=False)),
        description=description if isinstance(description, str) else None,
        id=identifier if isinstance(identifier, str) else None,
        minor=minor if isinstance(minor, int) else 0,
        extends=extends if isinstance(extends, str) else None,
    )


@functools.cache
def bundled_catalog(identifier: str) -> Catalog:
    """A catalog that ships with this package, by its id.

    Raises:
        ValueError: If no such catalog ships, or its file is not that catalog.
    """
    file = load_vocabulary().bundled_catalogs.get(identifier)
    if file is None:
        raise ValueError(f"{identifier!r} is no catalog this package ships")
    catalog = parse_catalog(_text(file), file)
    if catalog.id != identifier:
        raise ValueError(f"{file} is {catalog.id!r}, not {identifier!r}")
    return catalog


def base_catalog() -> Catalog:
    """The base catalog: what every renderer draws."""
    return bundled_catalog(load_vocabulary().base_catalog)


def orkestrator_catalog() -> Catalog:
    """The Orkestrator's catalog, as the snapshot this package ships."""
    return bundled_catalog(ORKESTRATOR_CATALOG_ID)


def register_catalog(catalog: Catalog) -> None:
    """Make a published catalog known here, so a blok written for it is checked against it.

    For an author's tooling (``arkitekt check``, the language server), which resolved it from
    its cache or fetched it. A catalog that ships with the package is never replaced.

    Raises:
        ValueError: If the catalog has no id, or redefines a component of what it extends.
    """
    if catalog.id is None:
        raise ValueError(f"the catalog {catalog.name!r} has no id to be known by")
    if catalog.id in load_vocabulary().bundled_catalogs:
        return
    previous = _registered.get(catalog.id)
    _registered[catalog.id] = catalog
    try:
        catalog_chain(catalog.id)
    except ValueError:
        if previous is None:
            del _registered[catalog.id]
        else:
            _registered[catalog.id] = previous
        raise


def forget_catalogs() -> None:
    """Forget every catalog :func:`register_catalog` was handed (for tests and long-lived tooling)."""
    _registered.clear()


def known_catalog(identifier: str) -> Catalog | None:
    """The catalog called ``identifier``: one that ships, or one registered; None when unknown."""
    if identifier in load_vocabulary().bundled_catalogs:
        return bundled_catalog(identifier)
    return _registered.get(identifier)


def known_catalogs() -> tuple[Catalog, ...]:
    """Every catalog known here: those that ship (the base first), then those registered."""
    return (*(bundled_catalog(identifier) for identifier in load_vocabulary().bundled_catalogs), *_registered.values())


def catalog_chain(target: "str | Catalog | None" = None) -> tuple[Catalog, ...]:
    """The catalogs a blok written for ``target`` is drawn with, the base first.

    ``target`` is a catalog's id, a catalog (one nothing published: it extends the base unless
    it says otherwise), or None for the base alone.

    Raises:
        ValueError: If the target or a catalog it extends is not known, or a catalog redefines
            a component of one it extends.
    """
    base = base_catalog()
    chain: list[Catalog] = []
    current: Catalog
    if target is None:
        current = base
    elif isinstance(target, Catalog):
        current = target
    else:
        named = known_catalog(target)
        if named is None:
            raise ValueError(f"the catalog {target!r} is not known here")
        current = named
    seen: set[str] = set()
    while current is not base and current.id != base.id:
        if current.id is not None:
            if current.id in seen:
                raise ValueError(f"the catalog {current.id!r} extends itself")
            seen.add(current.id)
        chain.append(current)
        if current.extends is None:
            break
        parent = known_catalog(current.extends)
        if parent is None:
            raise ValueError(f"the catalog {label_of(current)} extends {current.extends!r}, which is not known here")
        current = parent
    ordered = (base, *reversed(chain))
    owner: dict[str, tuple[ComponentSpec, str]] = {}
    for catalog in ordered:
        for component in catalog.components:
            previous = owner.get(component.name)
            if previous is not None and previous[0] != component:
                raise ValueError(f"the catalog {label_of(catalog)} redefines <{component.name}> of {previous[1]}: a catalog only adds")
            owner.setdefault(component.name, (component, label_of(catalog)))
    return ordered


@dataclass(frozen=True)
class BlokView:
    """What a blok is drawn with: the components of the catalog it is written for (and of those
    that one extends, down to the base), and the functions of the vocabulary plus those a
    catalog adds."""

    components: Mapping[str, ComponentSpec]
    functions: Mapping[str, Function]
    label: str
    complete: bool = True
    """Whether the components are every one the target draws, so an unknown one is a finding."""
    target: str | None = None
    """The id of the catalog the view is of; None for one nothing published."""
    foreign: Mapping[str, str] = field(default_factory=dict[str, str])
    """Per component of a known catalog the view is NOT of, that catalog's id (``catalog.target``)."""

    def component(self, name: str) -> ComponentSpec | None:
        """The component called ``name``, or None."""
        return self.components.get(name)


def _operation_function(name: str, catalog: Catalog) -> Function | None:
    operation = next((operation for operation in catalog.operations if operation.name == name), None)
    if operation is None:
        return None
    return Function(
        name=name,
        params=tuple(Param(key=argument.key, kind=argument.kind, required=argument.required) for argument in operation.arguments),
        returns=operation.returns,
        description=operation.description or "",
    )


def blok_view(target: "str | Catalog | None" = None, *, complete: bool = True) -> BlokView:
    """The view of a blok written for ``target`` (see :func:`catalog_chain`); the base's when None.

    A catalog's operations join the vocabulary's functions only under a function namespace
    (``fmt.quantity``): a new namespace is a new root, which is the language's to add.

    Raises:
        ValueError: If the target is not known here, or its chain is broken.
    """
    vocabulary = load_vocabulary()
    chain = catalog_chain(target)
    components: dict[str, ComponentSpec] = {}
    for catalog in chain:
        for component in catalog.components:
            components.setdefault(component.name, component)
    functions: dict[str, Function] = dict(vocabulary.functions)
    for catalog in chain[1:]:
        for operation in catalog.operations:
            namespace = operation.name.split(".", 1)[0]
            if operation.name in functions or namespace not in vocabulary.function_namespaces:
                continue
            function = _operation_function(operation.name, catalog)
            if function is not None:
                functions[operation.name] = function
    foreign: dict[str, str] = {}
    for catalog in known_catalogs():
        if catalog in chain or catalog.id is None:
            continue
        for component in catalog.components:
            if component.name not in components:
                foreign.setdefault(component.name, catalog.id)
    last = chain[-1]
    return BlokView(
        components=components,
        functions=functions,
        label=" + ".join(label_of(catalog) for catalog in chain),
        complete=complete,
        target=last.id,
        foreign=foreign,
    )


@functools.lru_cache(maxsize=1)
def _base_view() -> BlokView:
    return blok_view()


def default_view() -> BlokView:
    """The view of a blok that names no catalog: the base catalog's."""
    if _registered:
        return blok_view()  # what is foreign depends on what was registered
    return _base_view()


def orkestrator_view() -> BlokView:
    """The view of a blok written for the Orkestrator's catalog."""
    return blok_view(ORKESTRATOR_CATALOG_ID)


def is_catalog_target(target: str) -> bool:
    """Whether ``target`` can name a catalog at all: a published one's address, or the name of
    one registered in an organization."""
    vocabulary = load_vocabulary()
    return vocabulary.catalog_id.fullmatch(target) is not None or vocabulary.catalog_name.fullmatch(target) is not None


def catalog_text(file: str) -> str:
    """A file as shipped (``catalogs/base@3.json``), for a party that vendors it."""
    return _text(file)


def catalog_files() -> tuple[str, ...]:
    """Every catalog file this package ships, relative to ``arkitekt_spec/blok``, the base first."""
    return tuple(load_vocabulary().bundled_catalogs.values())


# --------------------------------------------------------------------------- who checks a catalog

_checking: contextvars.ContextVar[bool] = contextvars.ContextVar("arkitekt_spec_checking_catalogs", default=False)


def catalogs_checked() -> bool:
    """Whether a blok being declared is held to its catalog right now, beyond the language."""
    return _checking.get()


@contextlib.contextmanager
def checking_catalogs(enabled: bool = True) -> Generator[None]:
    """Hold every blok declared inside to its catalog too: component names, props, placement.

    Off by default: an app that registers its bloks is held to the language alone, as a server
    holds it, and whoever draws a blok checks it against the catalog it has. An author's
    tooling (``arkitekt check``, the language server) turns it on around loading the app.
    """
    token = _checking.set(enabled)
    try:
        yield
    finally:
        _checking.reset(token)


__all__ = [
    "BASE_NAME",
    "HTML_GROUP",
    "LANGUAGE_GROUP",
    "ORKESTRATOR_CATALOG_ID",
    "BlokView",
    "base_catalog",
    "blok_view",
    "bundled_catalog",
    "catalog_chain",
    "catalog_files",
    "catalog_text",
    "catalogs_checked",
    "checking_catalogs",
    "default_view",
    "forget_catalogs",
    "is_catalog_target",
    "known_catalog",
    "known_catalogs",
    "label_of",
    "orkestrator_catalog",
    "orkestrator_view",
    "parse_catalog",
    "register_catalog",
]
