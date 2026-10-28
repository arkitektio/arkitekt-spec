"""The catalogs a v2 blok renders with, and the Orkestrator's module.

A *catalog* is a versioned set; a blok declares the ones it needs (``catalogs=["ui@1",
"scene@1"]``). A *module* is a UI's own unversioned catalog, registered by that UI (the server's
``Module``); a blok names at most one, as its ``module``.

- ``catalogs/base@2.json``: the language every renderer implements (``if``, ``else``,
  ``foreach``, ``match``, ``suspense``, ``ask``, ...). Its functions are the vocabulary's. The
  spec owns it.
- The global catalogs, versioned and opt-in per renderer: ``ui@1``, ``scene@1``, ``logo@1``,
  ``structure@1``, ``html@1`` (only ``div``, ``p`` and ``span``) and ``shadcn@1`` (the shadcn
  families beyond ``ui``, ``Chart``, ``RawInspector``). The UI defines them: they are the
  contracts of ``@arkitektio/bsx``, and ``scripts/sync_standard_catalog.py`` copies their JSON in.
- ``modules/orkestrator.json``: the ``orkestrator`` module, what only the Orkestrator draws (the
  ``MikroScene`` parts).
"""

import functools
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from importlib import resources

from arkitekt_spec.blok.vocabulary import JSON, Function, Param, load_vocabulary
from arkitekt_spec.declare.catalogs import Catalog, ComponentSpec, parse_catalog_components

BASE_NAME = "base"
ORKESTRATOR_MODULE = "orkestrator"
"""The module of what only the Orkestrator draws, beside the standard catalogs."""
ORKESTRATOR_MODULE_FILE = "modules/orkestrator.json"
"""Where the Orkestrator's module ships, relative to ``arkitekt_spec/blok``."""
STANDARD_CATALOGS = ("ui", "scene", "logo", "structure", "html", "shadcn")


def _text(file: str) -> str:
    """A file under ``arkitekt_spec/blok``: ``catalogs/ui@1.json``, ``modules/orkestrator.json``."""
    return resources.files("arkitekt_spec.blok").joinpath(file).read_text(encoding="utf-8")


def _read(file: str) -> dict[str, JSON]:
    raw: JSON = json.loads(_text(file))
    if not isinstance(raw, dict):
        raise ValueError(f"{file} is not a catalog")
    return raw


def _catalog(raw: dict[str, JSON], file: str) -> Catalog:
    name = raw.get("name")
    version = raw.get("version")
    description = raw.get("description")
    if not isinstance(name, str) or (version is not None and (not isinstance(version, int) or isinstance(version, bool))):
        raise ValueError(f"{file} has no name, or a version that is not an integer")
    return Catalog(
        name=name,
        version=version,
        components=tuple(parse_catalog_components(raw, camel=False)),
        description=description if isinstance(description, str) else None,
    )


@functools.lru_cache(maxsize=1)
def base_catalog() -> Catalog:
    """base@2: the language components."""
    version = load_vocabulary().base_version
    file = f"catalogs/{BASE_NAME}@{version}.json"
    catalog = _catalog(_read(file), file)
    if catalog.name != BASE_NAME:
        raise ValueError(f"{file} is named {catalog.name!r}")
    return catalog


@functools.cache
def standard_catalog(name: str) -> Catalog:
    """A standard catalog (``ui``) at the version the vocabulary names.

    Raises:
        ValueError: If ``name`` is not a standard catalog.
    """
    version = load_vocabulary().catalogs.get(name)
    if version is None:
        raise ValueError(f"{name!r} is no standard catalog (catalogs: {', '.join(STANDARD_CATALOGS)})")
    file = f"catalogs/{name}@{version}.json"
    catalog = _catalog(_read(file), file)
    if catalog.name != name:
        raise ValueError(f"{file} is named {catalog.name!r}")
    return catalog


@functools.lru_cache(maxsize=1)
def orkestrator_module() -> Catalog:
    """The ``orkestrator`` module: what only the Orkestrator draws, as the spec writes it for
    Orkestrator to implement and register."""
    module = _catalog(_read(ORKESTRATOR_MODULE_FILE), ORKESTRATOR_MODULE_FILE)
    if module.name != ORKESTRATOR_MODULE or module.version is not None:
        raise ValueError(f"{ORKESTRATOR_MODULE_FILE} is not the unversioned {ORKESTRATOR_MODULE!r} module")
    return module


def standard_catalogs() -> tuple[Catalog, ...]:
    """Every standard catalog, in the vocabulary's order."""
    return tuple(standard_catalog(name) for name in STANDARD_CATALOGS)


def catalog_text(file: str) -> str:
    """A file as shipped (``catalogs/ui@1.json``, ``modules/orkestrator.json``), for a party
    that vendors it."""
    return _text(file)


def catalog_files() -> tuple[str, ...]:
    """Every spec-owned catalog file and the Orkestrator's module, relative to ``arkitekt_spec/blok``."""
    vocabulary = load_vocabulary()
    return (
        f"catalogs/{BASE_NAME}@{vocabulary.base_version}.json",
        *(f"catalogs/{name}@{version}.json" for name, version in vocabulary.catalogs.items()),
        ORKESTRATOR_MODULE_FILE,
    )


@dataclass(frozen=True)
class Shadow:
    """A component name two sources define: which one is drawn.

    A module's definition beats a standard catalog's (the app draws its own ``Slider``); the
    language's (base) is never shadowed; between two standard catalogs the first, in the
    vocabulary's order, is kept.
    """

    component: str
    catalog: str
    """The source that defines it first: base or a standard catalog (``ui@1``)."""
    module: str
    """The source that defines it again: a module, or a later standard catalog."""
    winner: str
    """The one of the two whose definition is used."""


@dataclass(frozen=True)
class BlokView:
    """What a v2 blok is checked against: the components of base, the standard catalogs and a
    module, and the functions of the vocabulary plus those a module adds."""

    components: Mapping[str, ComponentSpec]
    functions: Mapping[str, Function]
    label: str
    complete: bool = True
    """Whether the components are every one the renderer draws, so an unknown one is a finding."""
    shadows: Mapping[str, Shadow] = field(default_factory=dict[str, Shadow])
    """Per component name two sources define, which one is used (``catalog.shadow``)."""

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


def blok_view(catalogs: Sequence[Catalog] = (), *, standard: bool = True, complete: bool = True) -> BlokView:
    """The view base, the standard catalogs (unless ``standard`` is False) and ``catalogs`` (a
    module, an app's own) give.

    A name two of them define differently is drawn as :class:`Shadow` says (the module's over
    a standard catalog's; base's always) and recorded in ``shadows``; the validator warns
    ``catalog.shadow`` where a tree uses it.

    A module's operations join the vocabulary's functions only under a function namespace
    (``fmt.quantity``): a new namespace is a new root, which ships with a new vocab version.
    """
    vocabulary = load_vocabulary()
    base = base_catalog()
    sources = [base, *(standard_catalogs() if standard else ()), *catalogs]
    modules = {id(catalog) for catalog in catalogs}
    components: dict[str, ComponentSpec] = {}
    shadows: dict[str, Shadow] = {}
    for catalog in sources:
        for component in catalog.components:
            previous = components.get(component.name)
            if previous is None:
                components[component.name] = component
                continue
            if previous == component:
                continue
            kept = previous.catalog or "?"
            other = component.catalog or catalog.name
            wins = id(catalog) in modules and kept != _label(base)
            if wins:
                components[component.name] = component
            shadows[component.name] = Shadow(component=component.name, catalog=kept, module=other, winner=other if wins else kept)
    functions: dict[str, Function] = dict(vocabulary.functions)
    for catalog in catalogs:
        for operation in catalog.operations:
            namespace = operation.name.split(".", 1)[0]
            if operation.name in functions or namespace not in vocabulary.function_namespaces:
                continue
            function = _operation_function(operation.name, catalog)
            if function is not None:
                functions[operation.name] = function
    label = " + ".join(
        f"{catalog.name}@{catalog.version}" if catalog.version is not None else catalog.name for catalog in sources
    )
    return BlokView(components=components, functions=functions, label=label, complete=complete, shadows=shadows)


def _label(catalog: Catalog) -> str:
    return f"{catalog.name}@{catalog.version}" if catalog.version is not None else catalog.name


@functools.lru_cache(maxsize=1)
def default_view() -> BlokView:
    """The Orkestrator's view: base, every standard catalog and the ``orkestrator`` module."""
    return blok_view([orkestrator_module()])


__all__ = [
    "BASE_NAME",
    "ORKESTRATOR_MODULE",
    "ORKESTRATOR_MODULE_FILE",
    "STANDARD_CATALOGS",
    "BlokView",
    "Shadow",
    "base_catalog",
    "blok_view",
    "catalog_files",
    "catalog_text",
    "default_view",
    "orkestrator_module",
    "standard_catalog",
    "standard_catalogs",
]
