"""Catalogs: what a UI can render and evaluate, and the rules a blok is checked against.

A *catalog* is a UI app's capability manifest. It declares the components the renderer can
draw and the pure operations it can evaluate for port validators, effects and blok util calls.

Two layers, exactly as on the server:

- The **base catalog** (``base_v1.json``, a byte-for-byte copy of the server's
  ``rekuest_core/catalogs/base_v1.json``; a test diffs them when the server tree is present)
  is implicit for every definition and blok. Its operations are always available, positional
  call arguments resolve to its parameter names, and a UI catalog only ever *extends* it.
- A **UI catalog** adds components and further operations. It may not redefine a base
  operation, and two catalogs may only agree.

This module also holds the validation rules themselves, mirroring the server's
``facade.catalog_validation``. They take plain primitives rather than the generated pydantic
models so that they stay usable from anywhere -- see the import rule below.

.. warning::
   This module must import **nothing** from ``rekuest``. ``rekuest.protocol.schema`` imports
   ``rekuest.traits.ports`` while it is still executing, and that reaches this loader, so any
   ``rekuest`` import here is a cycle. ``tests/test_base_catalog.py`` enforces it as a
   source-level substring check, which is why even a sibling ``rekuest.catalogs.x`` import is
   refused and everything schema-free lives in this one file. Code that needs the generated
   models lives elsewhere: ``rekuest.client.catalogs`` for the half that speaks to the server,
   ``rekuest.blok.validate`` and ``rekuest.definition.catalogs`` for the rest. This package is
   now a leaf -- it is ``__init__.py`` and ``base_v1.json``, and nothing else.
"""

import functools
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from importlib import resources

BASE_CATALOG_NAME = "base"
BASE_CATALOG_VERSION = 1
BASE_CATALOG_ID = f"{BASE_CATALOG_NAME}@{BASE_CATALOG_VERSION}"
VALUE_KINDS = ("STRING", "INT", "FLOAT", "BOOL", "DICT", "LIST", "ANY", "CALLBACK")
"""The ``CatalogValueKind`` names, hard-coded so this module never imports the generated schema."""

CALLBACK_KIND = "CALLBACK"
"""The prop kind that must be bound via an agent call or a util call rather than a value."""


# --------------------------------------------------------------------------- specs


@dataclass(frozen=True)
class ArgumentSpec:
    """A parameter of an operation."""

    key: str
    kind: str
    required: bool
    description: str | None = None


@dataclass(frozen=True)
class OperationSpec:
    """A pure operation a UI can evaluate; ``arguments`` order is positional order."""

    name: str
    returns: str
    arguments: tuple[ArgumentSpec, ...]
    description: str | None = None

    @property
    def keys(self) -> tuple[str, ...]:
        """Parameter names in positional order."""
        return tuple(argument.key for argument in self.arguments)

    @property
    def required_keys(self) -> tuple[str, ...]:
        """Parameter names every call must pass, in positional order."""
        return tuple(argument.key for argument in self.arguments if argument.required)


@dataclass(frozen=True)
class PropSpec:
    """A prop a component accepts."""

    key: str
    kind: str
    required: bool
    description: str | None = None


@dataclass(frozen=True)
class ComponentSpec:
    """A component a UI can render."""

    name: str
    accepts_children: bool = False
    props: tuple[PropSpec, ...] = ()
    description: str | None = None

    def prop(self, key: str) -> PropSpec | None:
        """The prop called ``key``, or ``None``."""
        return next((prop for prop in self.props if prop.key == key), None)


@dataclass(frozen=True)
class Catalog:
    """One catalog, from any source: the vendored base manifest, an app declaration, or the server.

    ``registered`` mirrors the server's ``UICatalog.isRegistered``: a catalog that exists only
    as a name contributes nothing, and its operations are not authoritative.
    """

    name: str
    operations: tuple[OperationSpec, ...] = ()
    components: tuple[ComponentSpec, ...] = ()
    version: int | None = None
    description: str | None = None
    registered: bool = True


# --------------------------------------------------------------------------- the base manifest


def _parse(raw: dict) -> Catalog:  # type: ignore[type-arg]
    operations: list[OperationSpec] = []
    seen: set[str] = set()
    for entry in raw["operations"]:
        name = entry["name"]
        if not name or name in seen:
            raise ValueError(f"base catalog: duplicate or empty operation name {name!r}")
        seen.add(name)
        if entry["returns"] not in VALUE_KINDS:
            raise ValueError(f"base catalog: {name} returns unknown kind {entry['returns']!r}")
        arguments: list[ArgumentSpec] = []
        keys: set[str] = set()
        for argument in entry.get("arguments", []):
            if not argument["key"] or argument["key"] in keys:
                raise ValueError(f"base catalog: {name} has a duplicate or empty argument key {argument['key']!r}")
            if argument["kind"] not in VALUE_KINDS:
                raise ValueError(f"base catalog: {name}.{argument['key']} has unknown kind {argument['kind']!r}")
            keys.add(argument["key"])
            arguments.append(
                ArgumentSpec(
                    key=argument["key"],
                    kind=argument["kind"],
                    required=bool(argument.get("required", True)),
                    description=argument.get("description"),
                )
            )
        operations.append(
            OperationSpec(
                name=name,
                returns=entry["returns"],
                arguments=tuple(arguments),
                description=entry.get("description"),
            )
        )
    if raw["name"] != BASE_CATALOG_NAME:
        raise ValueError(f"base catalog: unexpected name {raw['name']!r}")
    return Catalog(
        name=raw["name"],
        version=int(raw["version"]),
        operations=tuple(operations),
        description=raw.get("description"),
    )


@functools.lru_cache(maxsize=1)
def load_base_catalog() -> Catalog:
    """The vendored manifest, parsed and validated (cached)."""
    text = resources.files(__package__).joinpath("base_v1.json").read_text(encoding="utf-8")
    return _parse(json.loads(text))


@functools.lru_cache(maxsize=1)
def base_operations() -> Mapping[str, OperationSpec]:
    """Base operations by name."""
    return {operation.name: operation for operation in load_base_catalog().operations}


def base_operation(name: str) -> OperationSpec | None:
    """The base operation called ``name``, or ``None`` if it is not a base operation."""
    return base_operations().get(name)


def is_base_operation(name: str) -> bool:
    """Whether ``name`` is provided by the base catalog."""
    return name in base_operations()


def base_version_named(name: str) -> int | None:
    """The base-catalog version ``name`` refers to, or ``None`` if it names another catalog.

    ``"base"`` means the version this client ships; ``"base@2"`` means 2.
    """
    if name == BASE_CATALOG_NAME:
        return BASE_CATALOG_VERSION
    prefix = f"{BASE_CATALOG_NAME}@"
    if name.startswith(prefix):
        suffix = name[len(prefix) :]
        if suffix.isdigit():
            return int(suffix)
    return None


# --------------------------------------------------------------------------- diagnostics

UNKNOWN_OPERATION = "unknown_operation"
UNKNOWN_CATALOG = "unknown_catalog"
BASE_CATALOG_DRIFT = "base_catalog_drift"

WARNING = "WARNING"
"""The only diagnostic level there is: a hard finding is raised, never recorded."""


@dataclass(frozen=True)
class Diagnostic:
    """A non-fatal finding, shaped like the server's ``DiagnosticModel``.

    ``level`` is a plain string rather than the generated ``DiagnosticLevel`` so that this
    module imports nothing; a parity test asserts the strings are members of that enum.
    """

    level: str
    code: str
    message: str
    path: str | None = None


class CatalogWarning(UserWarning):
    """A finding that does not block registration; the server stores it as a diagnostic.

    Escalate the whole soft tier with ``warnings.simplefilter("error", CatalogWarning)``.
    """


# --------------------------------------------------------------------------- resolution


@dataclass(frozen=True)
class CatalogView:
    """The operations and components in force for one validation pass.

    ``components`` is ``None`` when no catalog has registered any, meaning component names
    cannot be checked at all -- the base catalog has no components, so before a UI app
    registers some there is nothing to check against. Every component rule is skipped then,
    exactly as the server skips them.

    ``authoritative`` says whether the operation set is complete enough for an unknown
    operation to be a real finding. Base-only it is not: every extension operation would look
    unknown, so the client stays quiet and lets the server warn at upload.
    """

    operations: Mapping[str, OperationSpec]
    components: Mapping[str, ComponentSpec] | None
    label: str
    authoritative: bool


def check_extension_does_not_shadow_base(catalog: Catalog) -> None:
    """A UI catalog may add operations but never redefine a base one.

    Raises:
        ValueError: If the catalog defines an operation the base catalog already provides.
    """
    shadowed = sorted({operation.name for operation in catalog.operations} & set(base_operations()))
    if shadowed:
        raise ValueError(f"catalog {catalog.name!r} cannot redefine base operations {shadowed}")


def resolve_catalogs(catalogs: Sequence[Catalog], *, authoritative: bool) -> CatalogView:
    """The view in force for the base catalog extended by ``catalogs``.

    Two catalogs may provide the same operation or component only if they define it
    identically; a conflicting definition is a hard error, because the UI could not know
    which one to use.

    Raises:
        ValueError: If two catalogs define one operation or component differently.
    """
    operations: dict[str, OperationSpec] = dict(base_operations())
    operation_provider: dict[str, str] = {}
    components: dict[str, ComponentSpec] = {}
    component_provider: dict[str, str] = {}
    any_components = False

    for catalog in catalogs:
        for operation in catalog.operations:
            previous = operations.get(operation.name)
            if previous is not None and operation.name in operation_provider and previous != operation:
                raise ValueError(
                    f"operation {operation.name!r} is defined differently by catalogs "
                    f"{operation_provider[operation.name]!r} and {catalog.name!r}"
                )
            operations[operation.name] = operation
            operation_provider.setdefault(operation.name, catalog.name)

        if not catalog.components:
            continue
        any_components = True
        for component in catalog.components:
            previous_component = components.get(component.name)
            if previous_component is not None and previous_component != component:
                raise ValueError(
                    f"component {component.name!r} is defined differently by catalogs "
                    f"{component_provider[component.name]!r} and {catalog.name!r}"
                )
            components[component.name] = component
            component_provider.setdefault(component.name, catalog.name)

    return CatalogView(
        operations=operations,
        components=components if any_components else None,
        label=" + ".join([BASE_CATALOG_ID, *(catalog.name for catalog in catalogs)]),
        authoritative=authoritative,
    )


def base_only_view() -> CatalogView:
    """The offline view: base operations, no components, not authoritative."""
    return resolve_catalogs((), authoritative=False)


# --------------------------------------------------------------------------- rules


def check_call(
    operation: str,
    argument_keys: Iterable[str],
    view: CatalogView,
    owner: str,
) -> Diagnostic | None:
    """Check one util call against ``view``.

    A known operation must be called with argument keys it accepts and every required one; an
    unknown operation is a warning, and only when the view is authoritative.

    Returns:
        A warning diagnostic for an unknown operation, else ``None``.

    Raises:
        ValueError: If a known operation is called with an unaccepted or missing argument key.
    """
    spec = view.operations.get(operation)
    if spec is None:
        if not view.authoritative:
            return None
        return Diagnostic(
            level=WARNING,
            code=UNKNOWN_OPERATION,
            message=(
                f"{owner}: operation {operation!r} is not provided by the catalog "
                f"({view.label}); the UI cannot evaluate this call until it is registered"
            ),
            path=owner,
        )

    accepted = {argument.key: argument for argument in spec.arguments}
    passed = set(argument_keys)
    unknown = sorted(passed - set(accepted))
    if unknown:
        raise ValueError(f"{owner}: operation {operation!r} does not accept arguments {unknown}")
    missing = sorted(key for key, argument in accepted.items() if argument.required and key not in passed)
    if missing:
        raise ValueError(f"{owner}: operation {operation!r} requires arguments {missing}")
    return None


def check_component(
    component: str,
    prop_keys: Iterable[str],
    has_children: bool,
    callback_bindings: Mapping[str, bool],
    view: CatalogView,
    owner: str,
) -> None:
    """Check one component node against ``view``.

    Does nothing when ``view.components`` is ``None``: no catalog has registered components,
    so nothing is knowable and nothing is invented.

    Every node is checked the same way, including BSX's own ``foreach``: the server has no
    concept of it either, so it arrives there as an ordinary component name and a catalog
    that registered components must register it too. Exempting it here would let the client
    pass a tree the server rejects, which is the one thing a pre-flight check must not do.

    Args:
        component: The component's name.
        prop_keys: The prop keys the node carries.
        has_children: Whether the node has child nodes.
        callback_bindings: Per prop key, whether it is bound via an agent call or a util call.
        view: The catalog view in force.
        owner: The node's location, for error messages.

    Raises:
        ValueError: On an unknown component, children a component does not accept, an unknown
            or missing required prop, or an unbound ``CALLBACK`` prop.
    """
    if view.components is None:
        return

    spec = view.components.get(component)
    if spec is None:
        raise ValueError(f"{owner}: component {component!r} is not registered in catalog ({view.label})")
    if has_children and not spec.accepts_children:
        raise ValueError(f"{owner}: component {component!r} does not accept children")

    prop_specs = {prop.key: prop for prop in spec.props}
    present = set(prop_keys)
    unknown = sorted(present - set(prop_specs))
    if unknown:
        raise ValueError(f"{owner}: component {component!r} has no props {unknown}")
    missing = sorted(key for key, prop in prop_specs.items() if prop.required and key not in present)
    if missing:
        raise ValueError(f"{owner}: component {component!r} requires props {missing}")

    for key in sorted(present):
        prop_spec = prop_specs[key]
        if prop_spec.kind == CALLBACK_KIND and not callback_bindings.get(key, False):
            raise ValueError(
                f"prop {key!r} of {owner} is a CALLBACK prop and must be bound "
                "via agent_call or util_call"
            )


def check_catalog_names(names: Iterable[str], known: Iterable[str], owner: str) -> list[Diagnostic]:
    """Warn for every catalog name that resolves to nothing.

    ``base``/``base@1`` name the built-in catalog and are accepted silently; another base
    version, or a name no catalog is registered under, is a warning.

    This is a **definition-only** rule. A blok carries a single catalog name that the server
    creates on demand, so a blok never produces an ``unknown_catalog`` finding.
    """
    known_names = set(known)
    diagnostics: list[Diagnostic] = []
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        version = base_version_named(name)
        if version is not None:
            if version != BASE_CATALOG_VERSION:
                diagnostics.append(
                    Diagnostic(
                        level=WARNING,
                        code=UNKNOWN_CATALOG,
                        message=(
                            f"{owner}: catalog {name!r} was not applied: "
                            f"this client provides {BASE_CATALOG_ID}"
                        ),
                        path=owner,
                    )
                )
            continue
        if name not in known_names:
            diagnostics.append(
                Diagnostic(
                    level=WARNING,
                    code=UNKNOWN_CATALOG,
                    message=(
                        f"{owner}: catalog {name!r} was not applied: "
                        "it is not registered in this organization"
                    ),
                    path=owner,
                )
            )
    return diagnostics


# --------------------------------------------------------------------------- parity inventory


@dataclass(frozen=True)
class Rule:
    """One validation rule, and the server function this client mirrors it from.

    ``server`` names a function in the server's ``facade.catalog_validation``. The parity test
    asserts every rule site over there maps onto one of these -- coverage, not a bijection:
    one entry may cover several raise sites.
    """

    id: str
    server: str
    description: str


RULES: tuple[Rule, ...] = (
    Rule("unknown_operation", "_check_call", "An operation no catalog in force provides (warning)."),
    Rule("operation_arguments", "_check_call", "A known operation called with unaccepted or missing argument keys."),
    Rule("unknown_component", "_check_component", "A component no catalog in force registered."),
    Rule("component_children", "_check_component", "Children on a component that does not accept them."),
    Rule("component_props", "_check_component", "Unknown or missing required props on a component."),
    Rule("callback_prop_unbound", "_check_component", "A CALLBACK prop not bound via agent_call or util_call."),
    Rule("catalog_conflict", "resolve_operations", "Two catalogs defining one operation or component differently."),
    Rule("base_shadowed", "check_extension_does_not_shadow_base", "A UI catalog redefining a base operation."),
    Rule("unknown_catalog", "catalogs_for_definition", "A definition naming a catalog that resolves to nothing (warning)."),
)
