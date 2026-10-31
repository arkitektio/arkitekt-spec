"""Catalogs: what a UI can render and evaluate, and the rules a definition is held to.

A *catalog* is a UI app's capability manifest. It declares the components the renderer can
draw and the pure operations it can evaluate.

Blok trees are checked against a :class:`~arkitekt_spec.blok.catalogs.BlokView` built from these
types. What stays on the rules here is a definition's port calls and CUSTOM widgets: they feed
its hash, so they stay on base@1.

- The **base catalog** (``base_v1.json``, a byte-for-byte copy of the server's
  ``rekuest_core/catalogs/base_v1.json``; a test diffs them when the server tree is present)
  is implicit for every definition. Its operations are always available, positional call
  arguments resolve to its parameter names, and a UI catalog only ever *extends* it.
- A **UI catalog** adds components and further operations. It may not redefine a base
  operation, and two catalogs may only agree.

The rules mirror the server's ``facade.catalog_validation``. They take plain primitives rather
than pydantic models, and this module imports nothing else of the package, because
``arkitekt_spec.declare.traits`` reaches it while the action models are still being built.
"""

import functools
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from importlib import resources
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from arkitekt_spec.blok.vocabulary import Finding

BASE_CATALOG_NAME = "base"
BASE_CATALOG_VERSION = 1
BASE_CATALOG_ID = f"{BASE_CATALOG_NAME}@{BASE_CATALOG_VERSION}"
VALUE_KINDS = ("STRING", "INT", "FLOAT", "BOOL", "DICT", "LIST", "ANY", "CALLBACK", "ASSET")

ASSET_KIND = "ASSET"
"""The prop kind that names a file an app registered: bound to ``<dependency>.assets.<name>``."""
ASSETS_SEGMENT = "assets"
"""What follows a dependency's key in the path of one of its assets. No state can be called this."""
MODEL_REF_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
"""A file's reference: the algorithm, and the hex SHA-256 of its exact bytes."""


def asset_path_parts(path: str) -> tuple[str, str] | None:
    """The ``(dependency key, asset name)`` of an asset path, or None if ``path`` is not one.

    An asset is named like everything else of a dependency, key first:
    ``self.assets.scope``.

    Raises:
        ValueError: If the path reaches into a dependency's assets and is not
            ``<dependency>.assets.<name>``.
    """
    parts = path.split(".")
    if len(parts) < 2 or parts[1] != ASSETS_SEGMENT:
        return None
    if len(parts) != 3 or not all(parts):
        raise ValueError(f"an asset is named '<dependency>.assets.<name>', not {path!r}")
    return parts[0], parts[2]

HTML_CONTEXT = "HTML"
SCENE_CONTEXT = "SCENE"
LOGO_CONTEXT = "LOGO"
COMPONENT_CONTEXTS = (HTML_CONTEXT, SCENE_CONTEXT, LOGO_CONTEXT)
"""The renderers a component can live in. A blok's tree starts in the html context."""
ACTION_LOGO_CONTEXT = "ACTION_LOGO"
"""A blok context only, never a component's: an action logo draws the same shapes as a logo."""
LOGO_CONTEXTS = (LOGO_CONTEXT, ACTION_LOGO_CONTEXT)
"""The blok contexts held to the size of an icon."""
BLOK_ARGS_ROOT = "args"
"""The path root a blok reads the items it is opened for at (``@args.image``): null until it is opened for one."""

LOGO_PAINT_TOKENS = ("ink", "muted", "brand", "brand-strong", "brand-soft", "surface", "none")
"""The palette: colours of the viewer's theme, never of the logo's own.

Orkestrator draws each from its brand hue (``var(--logo-<token>)``), so a logo painted
with them follows the viewer's brand and dark mode. A logo may use them beside any SVG
colour; an action logo paints with nothing else, but a ``url(#id)`` to a gradient or
clip path, whose stops are held to the same tokens."""
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
class EventFieldSpec:
    """A field of the payload a component hands a callback prop (``event.<key>``)."""

    key: str
    kind: str
    description: str | None = None


@dataclass(frozen=True)
class PropSpec:
    """A prop a component accepts."""

    key: str
    kind: str
    required: bool
    description: str | None = None
    event: tuple[EventFieldSpec, ...] | None = None
    """For a ``CALLBACK`` prop of a v2 catalog, the payload it is called with; ``()`` when none."""
    values: tuple[str, ...] | None = None
    """For a ``STRING`` prop, the closed set of literals the component draws; None: any string."""


@dataclass(frozen=True)
class ComponentSpec:
    """A component a UI can render."""

    name: str
    accepts_children: bool = False
    props: tuple[PropSpec, ...] = ()
    description: str | None = None
    context: str = HTML_CONTEXT
    """The context the component may be placed in."""
    children_context: str | None = None
    """The context of its children when it differs from its own: it bridges two renderers."""
    inherits_context: bool = False
    """Placed in any context, taking its parent's for itself and its children (control flow)."""
    downstream_of: tuple[str, ...] = ()
    """Components of which at least one must be an ancestor, at any depth."""
    not_downstream_of: tuple[str, ...] = ()
    """Components of which none may be an ancestor."""
    text_prop: str | None = None
    """The prop text children fill (``<Button>Grip</Button>`` sets ``label``); None: it takes no text."""
    catalog: str | None = dataclass_field(default=None, compare=False)
    """The catalog that provides it, as ``name@version`` (``ui@1``)."""

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
        components=tuple(_parse_components(raw, raw.get("components", []), camel=False)),
        description=raw.get("description"),
    )


@functools.lru_cache(maxsize=1)
def load_base_catalog() -> Catalog:
    """The vendored manifest, parsed and validated (cached)."""
    text = resources.files(__package__ or "arkitekt_spec.declare.catalogs").joinpath("base_v1.json").read_text(encoding="utf-8")
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


@functools.lru_cache(maxsize=1)
def base_components() -> Mapping[str, ComponentSpec]:
    """Base components by name: control flow, named in lowercase."""
    return {component.name: component for component in load_base_catalog().components}


def is_base_component(name: str) -> bool:
    """Whether the component ``name`` is provided by the base catalog."""
    return name in base_components()


# --------------------------------------------------------------------------- the standard catalog

STANDARD_CATALOG_NAME = "orkestrator"
"""The catalog a blok is checked against when it names none: what the Orkestrator app renders."""

def _parse_context(raw: dict, component: str, value: object) -> str | None:  # type: ignore[type-arg]
    if value is None:
        return None
    if value not in COMPONENT_CONTEXTS:
        raise ValueError(f"catalog {raw.get('name')!r}: {component} has unknown context {value!r}")
    return str(value)


def _parse_components(raw: dict, entries: Iterable[dict], *, camel: bool) -> list[ComponentSpec]:  # type: ignore[type-arg]
    """Component entries as :class:`ComponentSpec`: a published catalog spells its keys in camelCase, the base manifest in snake_case."""

    def field(entry: dict, snake: str) -> object:  # type: ignore[type-arg]
        head, *rest = snake.split("_")
        return entry.get(head + "".join(part.title() for part in rest) if camel else snake)

    components: list[ComponentSpec] = []
    seen: set[str] = set()
    for entry in entries:
        name = entry["name"]
        if not name or name in seen:
            raise ValueError(f"catalog {raw.get('name')!r}: duplicate or empty component name {name!r}")
        seen.add(name)
        props: list[PropSpec] = []
        keys: set[str] = set()
        for prop in entry.get("props", []):
            if prop["key"] in keys:
                raise ValueError(f"catalog {raw.get('name')!r}: {name} repeats the prop {prop['key']!r}")
            if prop["kind"] not in VALUE_KINDS:
                raise ValueError(
                    f"catalog {raw.get('name')!r}: {name}.{prop['key']} has unknown kind {prop['kind']!r}"
                )
            keys.add(prop["key"])
            event = prop.get("event")
            values = prop.get("values")
            if values is not None and (not isinstance(values, list) or not all(isinstance(value, str) for value in values)):
                raise ValueError(f"catalog {raw.get('name')!r}: {name}.{prop['key']} has values that are not a list of strings")
            props.append(
                PropSpec(
                    key=prop["key"],
                    kind=prop["kind"],
                    required=bool(prop.get("required", False)),
                    description=prop.get("description"),
                    event=(
                        tuple(
                            EventFieldSpec(key=field["key"], kind=field["kind"], description=field.get("description"))
                            for field in event
                        )
                        if isinstance(event, list)
                        else None
                    ),
                    values=tuple(values) if values is not None else None,  # pyright: ignore[reportUnknownArgumentType]
                )
            )
        version = raw.get("version")
        components.append(
            ComponentSpec(
                name=name,
                accepts_children=bool(field(entry, "accepts_children") or False),
                props=tuple(props),
                description=entry.get("description"),
                context=_parse_context(raw, name, entry.get("context")) or HTML_CONTEXT,
                children_context=_parse_context(raw, name, field(entry, "children_context")),
                inherits_context=bool(field(entry, "inherits_context") or False),
                downstream_of=tuple(field(entry, "downstream_of") or ()),  # type: ignore[arg-type]
                not_downstream_of=tuple(field(entry, "not_downstream_of") or ()),  # type: ignore[arg-type]
                text_prop=field(entry, "text_prop") or None,  # type: ignore[arg-type]
                catalog=f"{raw.get('name')}@{version}" if version is not None else raw.get("name"),
            )
        )

    return components


def parse_catalog_components(raw: "Mapping[str, object]", *, camel: bool) -> list[ComponentSpec]:
    """Every component of a catalog payload, base ones included: a published catalog spells its
    keys in camelCase, the spec's own files (base, the v2 catalogs, the orkestrator module) in snake_case.

    Raises:
        ValueError: If two components share a name, or a prop has an unknown kind or context.
    """
    try:
        entries: object = raw.get("components", [])
        return _parse_components(dict(raw), entries if isinstance(entries, list) else [], camel=camel)  # pyright: ignore[reportUnknownArgumentType]
    except (KeyError, TypeError) as error:
        raise ValueError(f"not a catalog payload: {error!r}") from error


def _parse_ui_catalog(raw: dict) -> Catalog:  # type: ignore[type-arg]
    """A ``registerUiCatalog`` payload, as a UI app publishes it, as a :class:`Catalog`.

    Operations and components the base catalog already provides are left out: a UI catalog
    only extends the base, and a published one lists everything the app evaluates, base included.
    """
    components = [
        component for component in _parse_components(raw, raw.get("components", []), camel=True) if not is_base_component(component.name)
    ]

    operations: list[OperationSpec] = []
    for entry in raw.get("operations", []):
        if is_base_operation(entry["name"]):
            continue
        operations.append(
            OperationSpec(
                name=entry["name"],
                returns=entry["returns"],
                arguments=tuple(
                    ArgumentSpec(
                        key=argument["key"],
                        kind=argument["kind"],
                        required=bool(argument.get("required", False)),
                        description=argument.get("description"),
                    )
                    for argument in entry.get("arguments", [])
                ),
                description=entry.get("description"),
            )
        )

    return Catalog(
        name=raw["name"],
        operations=tuple(operations),
        components=tuple(components),
        description=raw.get("description"),
    )


def is_standard_catalog(name: str | None) -> bool:
    """Whether a blok naming ``name`` is checked against the standard catalog: it names it, or none."""
    return name is None or name == STANDARD_CATALOG_NAME


def parse_ui_catalog(raw: dict) -> Catalog:  # type: ignore[type-arg]
    """A ``blok-catalog.json`` payload as a :class:`Catalog`.

    Raises:
        ValueError: If the payload is not a valid catalog.
    """
    try:
        return _parse_ui_catalog(raw)
    except (KeyError, TypeError) as error:
        raise ValueError(f"not a UI catalog payload: {error!r}") from error


# --------------------------------------------------------------------------- diagnostics

UNKNOWN_OPERATION = "unknown_operation"
OPERATION_ARGUMENTS = "operation_arguments"
UNKNOWN_COMPONENT = "unknown_component"
COMPONENT_CHILDREN = "component_children"
COMPONENT_PROPS = "component_props"
CALLBACK_PROP_UNBOUND = "callback_prop_unbound"
ASSET_PROP = "asset_prop"

WARNING = "WARNING"
"""The only diagnostic level there is: a hard finding is raised, never recorded."""

BlokCheckMode = Literal["warning", "strict"]
"""How a blok is held to its catalog on the client.

``"warning"`` (the default) reports an unknown component, a missing or unknown prop and
every other catalog finding as a warning: the client's copy of a catalog may lag the UI
that renders the blok. ``"strict"`` refuses the blok on the first one.
"""


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


def _finding(findings: "list[Diagnostic] | None", code: str, message: str, owner: str) -> None:
    """Record a catalog finding in ``findings``, or raise it when there is no list to record it in."""
    if findings is None:
        raise ValueError(message)
    findings.append(Diagnostic(level=WARNING, code=code, message=message, path=owner))


class CatalogWarning(UserWarning):
    """A finding that does not block registration; the server stores it as a diagnostic.

    Escalate the whole soft tier with ``warnings.simplefilter("error", CatalogWarning)``.

    Args:
        message: What is told, the blok named in front.
        finding: The finding it tells, with its code, node path and place in the source, for a
            reader that places it (``arkitekt check --json-out``) rather than prints it.
        blok: The key of the blok it is about (``monitor``, or an action's interface).
    """

    def __init__(self, message: str, *, finding: "Finding | None" = None, blok: str | None = None) -> None:
        super().__init__(message)
        self.finding = finding
        self.blok = blok


# --------------------------------------------------------------------------- resolution


@dataclass(frozen=True)
class CatalogView:
    """The operations and components in force for one validation pass.

    ``components`` is ``None`` when no catalog has registered any, meaning component names
    cannot be checked at all -- the base catalog's control flow says nothing about what a UI
    draws, so before a UI app registers some there is nothing to check against. Every component rule is skipped then,
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
    """A UI catalog may add operations and components but never redefine a base one.

    Raises:
        ValueError: If the catalog defines an operation or component the base catalog already provides.
    """
    shadowed = sorted({operation.name for operation in catalog.operations} & set(base_operations()))
    if shadowed:
        raise ValueError(f"catalog {catalog.name!r} cannot redefine base operations {shadowed}")
    shadowed = sorted({component.name for component in catalog.components} & set(base_components()))
    if shadowed:
        raise ValueError(f"catalog {catalog.name!r} cannot redefine base components {shadowed}")


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
    components: dict[str, ComponentSpec] = dict(base_components())
    component_provider: dict[str, str] = {name: BASE_CATALOG_ID for name in components}
    any_components = False

    for catalog in catalogs:
        for operation in catalog.operations:
            # Base wins: a catalog published before an operation joined base still lists it.
            if is_base_operation(operation.name):
                continue
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
            if is_base_component(component.name):
                continue
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


# --------------------------------------------------------------------------- rules


def check_call(
    operation: str,
    argument_keys: Iterable[str],
    view: CatalogView,
    owner: str,
    findings: list[Diagnostic] | None = None,
) -> Diagnostic | None:
    """Check one util call against ``view``.

    A known operation must be called with argument keys it accepts and every required one; an
    unknown operation is a warning, and only when the view is authoritative.

    Returns:
        A warning diagnostic for an unknown operation, else ``None``.

    Raises:
        ValueError: If a known operation is called with an unaccepted or missing argument
            key, and there are no ``findings`` to record it in.
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
        _finding(findings, OPERATION_ARGUMENTS, f"{owner}: operation {operation!r} does not accept arguments {unknown}", owner)
    missing = sorted(key for key, argument in accepted.items() if argument.required and key not in passed)
    if missing:
        _finding(findings, OPERATION_ARGUMENTS, f"{owner}: operation {operation!r} requires arguments {missing}", owner)
    return None


def check_component(
    component: str,
    prop_keys: Iterable[str],
    has_children: bool,
    callback_bindings: Mapping[str, bool],
    view: CatalogView,
    owner: str,
    bound_paths: Mapping[str, str] | None = None,
    findings: list[Diagnostic] | None = None,
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
        bound_paths: Per prop key, the path the prop is bound to, for the props bound to one.
        findings: Where to record what is wrong, as warnings, instead of raising it.

    Raises:
        ValueError: On an unknown component, children a component does not accept, an unknown
            or missing required prop, an unbound ``CALLBACK`` prop, or an ``ASSET`` prop
            that does not name an asset -- unless there are ``findings`` to record it in.
    """
    if view.components is None:
        return

    spec = view.components.get(component)
    if spec is None:
        message = f"{owner}: component {component!r} is not registered in catalog ({view.label})"
        _finding(findings, UNKNOWN_COMPONENT, message, owner)
        return
    if has_children and not spec.accepts_children:
        _finding(findings, COMPONENT_CHILDREN, f"{owner}: component {component!r} does not accept children", owner)

    prop_specs = {prop.key: prop for prop in spec.props}
    present = set(prop_keys)
    unknown = sorted(present - set(prop_specs))
    if unknown:
        _finding(findings, COMPONENT_PROPS, f"{owner}: component {component!r} has no props {unknown}", owner)
    missing = sorted(key for key, prop in prop_specs.items() if prop.required and key not in present)
    if missing:
        _finding(findings, COMPONENT_PROPS, f"{owner}: component {component!r} requires props {missing}", owner)

    for key in sorted(present & set(prop_specs)):
        prop_spec = prop_specs[key]
        if prop_spec.kind == CALLBACK_KIND and not callback_bindings.get(key, False):
            message = f"prop {key!r} of {owner} is a CALLBACK prop and must be bound via agent_call or util_call"
            _finding(findings, CALLBACK_PROP_UNBOUND, message, owner)
        if prop_spec.kind == ASSET_KIND:
            path = (bound_paths or {}).get(key)
            try:
                named = asset_path_parts(path) if path is not None else None
            except ValueError as error:
                _finding(findings, ASSET_PROP, f"prop {key!r} of {owner}: {error}", owner)
                continue
            if named is None:
                message = (
                    f"prop {key!r} of {owner} is an ASSET prop and must name an asset "
                    f"the app registered, as in {key}=\"@self.assets.<name>\""
                )
                _finding(findings, ASSET_PROP, message, owner)
