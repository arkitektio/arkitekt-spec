"""The structural rules of the action language that need nothing but the models.

Mixed into the models of :mod:`arkitekt_spec.actions`, so they run wherever a
declaration is read -- rekuest building one, the CLI recording one, a server
ingesting one. Checks that need more than the models (parsing a search query,
resolving a state reference, validating a blok component) are the consumer's.

This module never imports :mod:`arkitekt_spec.actions` at runtime (the models
inherit from it), so enum-valued fields are compared by value: the models store
them as plain strings (``use_enum_values=True``).
"""

import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from typing import Any, Protocol, Self, cast

from pydantic import BaseModel, field_validator, model_validator

#: The reserved ``value_path`` root that refers to the port's own value.
OWN_VALUE = "value"

#: The ``value_path`` roots of a runtime formula: the form's values and the agent's.
ESTIMATE_ARGS_ROOT = "args"
ESTIMATE_AGENT_ROOT = "agent"

#: What a runtime formula reads of the agent's hardware (``agent.cpu_count``).
AGENT_HARDWARE = ("cpu_count", "memory_mib", "gpu_count", "gpu_memory_mib")

#: What a ``Fits`` may be made of: what compiles to a ``jsonpath`` and evaluates alike everywhere.
FITS_ARITHMETIC = frozenset({"math.add", "math.sub", "math.mul", "math.div", "math.neg", "math.abs", "math.floor", "math.ceil"})
FITS_COMPARISONS = frozenset({"cmp.gt", "cmp.gte", "cmp.lt", "cmp.lte"})
FITS_COMBINATIONS = frozenset({"logic.all", "logic.any"})

#: Separator of a port path: ``foo..bar`` is the child ``bar`` of port ``foo``.
PORT_PATH_SEPARATOR = ".."

#: The mutually exclusive ways an ``ActionArgumentInput`` can be bound.
ARGUMENT_BINDINGS = (
    "value_literal",
    "value_path",
    "agent_call",
    "util_call",
    "value_list",
    "value_dict",
)

#: The conventional key of a LIST or DICT item port.
ITEM_KEY = "..."


class _Argument(Protocol):
    key: str | None
    value_path: str | None
    value_literal: Any
    util_call: Any
    agent_call: Any
    value_list: Any
    value_dict: Any


class _Call(Protocol):
    operation: str
    arguments: Any


def _value(kind: Any) -> Any:  # noqa: ANN401 -- an enum member or its value
    return getattr(kind, "value", kind)


def value_path_root(value_path: str) -> str:
    """The first segment of a JSON-pointer-like value path (``'/other/x'`` -> ``'other'``)."""
    return value_path.lstrip("/").split("/", 1)[0]


def iter_call_arguments(call: _Call) -> Iterator[_Argument]:
    """Every argument node in ``call``, depth first, through nested calls, lists and dicts."""

    def walk(arguments: Iterable[_Argument] | None) -> Iterator[_Argument]:
        for argument in arguments or ():
            yield argument
            if argument.util_call is not None:
                yield from walk(argument.util_call.arguments)
            if argument.agent_call is not None:
                yield from walk(argument.agent_call.arguments)
            yield from walk(argument.value_list)
            yield from walk(argument.value_dict)

    yield from walk(call.arguments)


def infer_dependencies(call: _Call, explicit: Iterable[str] | None = None) -> tuple[str, ...]:
    """The ports a call subscribes to: every ``value_path`` root except :data:`OWN_VALUE`.

    First-seen order without duplicates; ``explicit`` entries not already present
    are appended.
    """
    seen: dict[str, None] = {}
    for argument in iter_call_arguments(call):
        if argument.value_path is None:
            continue
        root = value_path_root(argument.value_path)
        if root and root != OWN_VALUE:
            seen.setdefault(root, None)
    for name in explicit or ():
        seen.setdefault(name, None)
    return tuple(seen)


def _check_keyed(arguments: Iterable[_Argument] | None, owner: str) -> None:
    seen: set[str] = set()
    for argument in arguments or ():
        if not argument.key:
            raise ValueError(f"{owner}: every entry must carry a key")
        if argument.key in seen:
            raise ValueError(f"{owner}: duplicate key {argument.key!r}")
        seen.add(argument.key)


def check_call_shape(call: _Call, owner: str) -> None:
    """Raise unless ``call`` has the argument shape the server accepts.

    The operation is named; call arguments and ``value_dict`` entries carry unique,
    non-empty keys; ``value_list`` entries carry none; every argument is bound in
    exactly one way. Nested util calls are checked the same way.
    """
    if not call.operation or not call.operation.strip():
        raise ValueError(f"{owner} must name an operation")
    _check_keyed(call.arguments, f"{owner}: arguments of {call.operation}")

    for argument in call.arguments or ():
        bound = [name for name in ARGUMENT_BINDINGS if getattr(argument, name) is not None]
        if len(bound) != 1:
            raise ValueError(
                f"{owner}: argument {argument.key!r} must set exactly one of "
                f"{', '.join(ARGUMENT_BINDINGS)} (got {bound or 'none'})"
            )
        _check_keyed(argument.value_dict, f"{owner}: value_dict of argument {argument.key!r}")
        for entry in argument.value_list or ():
            if entry.key is not None:
                raise ValueError(
                    f"{owner}: value_list entries of argument {argument.key!r} must not carry a key"
                )
        if argument.util_call is not None:
            check_call_shape(argument.util_call, owner)
        for nested in (*(argument.value_list or ()), *(argument.value_dict or ())):
            if nested.util_call is not None:
                check_call_shape(nested.util_call, owner)


def check_pure_call(call: _Call, dependencies: Iterable[str] | None, owner: str) -> None:
    """Raise unless ``call`` is pure and only references ``dependencies``.

    A valid shape, no agent call anywhere in the argument tree, and every
    ``value_path`` rooted at :data:`OWN_VALUE` or a declared dependency.
    """
    check_call_shape(call, owner)
    allowed = set(dependencies or ()) | {OWN_VALUE}
    for argument in iter_call_arguments(call):
        if argument.agent_call is not None:
            raise ValueError(f"{owner} must be pure: nested agent calls are not allowed")
        if argument.value_path is not None:
            root = value_path_root(argument.value_path)
            if root not in allowed:
                raise ValueError(
                    f"{owner} references '{root}' via value_path but it is not in dependencies "
                    "(declare it with dependencies=[...], or build the call with "
                    "withValidator/withEffect, which infer it)"
                )


def _pointer_segment(segment: str) -> str:
    """The text of one RFC 6901 segment of a ``value_path``."""
    return segment.replace("~1", "/").replace("~0", "~")


def check_estimated_runtime(call: _Call, ports: Iterable[Any], owner: str) -> None:
    """Raise unless ``call`` is a number the server can compute when matching actions.

    The action query filters and sorts by it for one object, so it reads the descriptors of
    one structure argument (``args/<port>/@pkg~1key``) and ``agent/<one of AGENT_HARDWARE>``,
    and only uses what a Postgres ``jsonpath`` has. ``estimated_runtime_ui`` has the rest.
    """
    hint = "; a formula using more goes in estimated_runtime_ui"
    check_call_shape(call, owner)
    if call.operation not in FITS_ARITHMETIC:
        raise ValueError(f"{owner}: {call.operation} cannot be computed when matching actions, which only has {', '.join(sorted(FITS_ARITHMETIC))}{hint}")
    by_key = {port.key: port for port in ports}
    read: set[str] = set()

    def check_read(path: str) -> None:
        segments = path.split("/")
        if segments[0] == ESTIMATE_ARGS_ROOT:
            port = by_key.get(segments[1]) if len(segments) > 1 else None
            if len(segments) != 3 or not segments[2].startswith("@") or port is None or not port.identifier:
                raise ValueError(f"{owner} reads '{path}': it can read the descriptors of a structure argument, args.<port>[\"@pkg/key\"]{hint}")
            read.add(segments[1])
            if len(read) > 1:
                raise ValueError(f"{owner} reads {' and '.join(sorted(read))}: the action query has one object, so it reads one argument{hint}")
        elif segments[0] != ESTIMATE_AGENT_ROOT or len(segments) != 2 or segments[1] not in AGENT_HARDWARE:
            raise ValueError(f"{owner} reads '{path}': it can read a structure argument's descriptors and agent.{{{', '.join(AGENT_HARDWARE)}}}{hint}")

    try:
        for argument in call.arguments or ():
            _check_formula_number(argument, owner, check_read)
    except ValueError as error:
        raise ValueError(f"{error}{'' if hint in str(error) else hint}") from error


def check_estimated_runtime_ui(call: _Call, ports: Iterable[Any], owner: str) -> None:
    """Raise unless ``call`` is pure and reads only what the form's estimate may read.

    Only the frontend evaluates it, against the form and the agent, so its paths are
    ``args/<port>`` (nested fields below it), ``args/<port>/@pkg~1key`` (a descriptor of the
    structure picked there) and ``agent/<one of AGENT_HARDWARE>``.
    """
    check_call_shape(call, owner)
    by_key = {port.key: port for port in ports}
    for argument in iter_call_arguments(call):
        if argument.agent_call is not None:
            raise ValueError(f"{owner} must be pure: nested agent calls are not allowed")
        if argument.value_path is None:
            continue
        root, *rest = argument.value_path.split("/")
        if root == ESTIMATE_ARGS_ROOT and rest:
            port = by_key.get(rest[0])
            if port is None:
                raise ValueError(f"{owner} reads args.{rest[0]}, but the action has no such argument")
            descriptors = [_pointer_segment(segment) for segment in rest[1:] if segment.startswith("@")]
            if descriptors and (len(rest) != 2 or not port.identifier):
                raise ValueError(f"{owner} reads the descriptor {descriptors[0]} of args.{rest[0]}, which only a structure argument has")
        elif root == ESTIMATE_AGENT_ROOT and rest:
            if len(rest) != 1 or rest[0] not in AGENT_HARDWARE:
                raise ValueError(f"{owner} reads agent.{'.'.join(rest)}; an agent has {', '.join(AGENT_HARDWARE)}")
        else:
            raise ValueError(f"{owner} reads '{argument.value_path}': a path starts with args.<port> or agent.")


def _check_fits_read(path: str, owner: str) -> None:
    segments = path.split("/")
    if segments[0] == OWN_VALUE:
        if len(segments) != 2 or not segments[1].startswith("@"):
            raise ValueError(f"{owner} reads '{path}': name a descriptor of this argument, such as @mikro/size")
    elif segments[0] != ESTIMATE_AGENT_ROOT or len(segments) != 2 or segments[1] not in AGENT_HARDWARE:
        raise ValueError(f"{owner} reads '{path}': it can read this argument's descriptors and agent.{{{', '.join(AGENT_HARDWARE)}}}. Agent state and other arguments cannot be tested when matching actions")


def _check_formula_number(argument: _Argument, owner: str, check_read: Callable[[str], None]) -> None:
    """Hold one operand to what a database predicate can compute."""
    if argument.util_call is not None:
        if argument.util_call.operation not in FITS_ARITHMETIC:
            raise ValueError(f"{owner}: {argument.util_call.operation} cannot be computed when matching actions, which only has {', '.join(sorted(FITS_ARITHMETIC))}")
        for nested in argument.util_call.arguments or ():
            _check_formula_number(nested, owner, check_read)
    elif argument.value_path is not None:
        check_read(argument.value_path)
    elif isinstance(argument.value_literal, bool) or not isinstance(argument.value_literal, (int, float)):
        raise ValueError(f"{owner} uses {argument.value_literal!r}: it only works on numbers")


def _check_fits_predicate(call: _Call, owner: str) -> None:
    if call.operation in FITS_COMPARISONS:
        for argument in call.arguments or ():
            _check_formula_number(argument, owner, lambda path: _check_fits_read(path, owner))
    elif call.operation in FITS_COMBINATIONS:
        for argument in call.arguments or ():
            for item in argument.value_list or ():
                if item.util_call is None:
                    raise ValueError(f"{owner}: and/or combine comparisons")
                _check_fits_predicate(item.util_call, owner)
    else:
        raise ValueError(f"{owner} must be a comparison ({', '.join(sorted(FITS_COMPARISONS))}), or several joined by and/or, not {call.operation}. Exact values belong in Requires")


def check_fits(call: _Call, owner: str) -> None:
    """Raise unless ``call`` compiles to the database predicate that matches actions.

    The server, the agent and the frontend all evaluate it, so it stays where they agree: a
    comparison of numbers, on a missing value false everywhere. That leaves out ``not``, ``==``
    and ``!=``, which disagree on a missing value, and ``**``, ``sqrt`` and ``log``, which a
    Postgres ``jsonpath`` does not have.
    """
    check_call_shape(call, owner)
    _check_fits_predicate(call, owner)


def resolve_port_path(path: str, ports: Iterable[Any]) -> bool:
    """True if a port path (``a..b..c``) resolves through ``children`` from the given roots."""
    candidates = list(ports)
    for segment in path.split(PORT_PATH_SEPARATOR):
        match = next((port for port in candidates if port.key == segment), None)
        if match is None:
            return False
        candidates = list(match.children or ())
    return True


def _ensure_unique(values: Iterable[str], label: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"Duplicate {label} '{value}' in agent input")
        seen.add(value)


# --- The mixins -------------------------------------------------------------


class PortRules(BaseModel):
    """A port: a JSON default, and the per-kind structure."""

    @field_validator("default", check_fields=False)
    @classmethod
    def _default_is_json(cls, value: Any) -> Any:  # noqa: ANN401
        if value is None or isinstance(value, (str, int, float, dict, list, bool)):
            return value  # pyright: ignore[reportUnknownVariableType]
        raise ValueError(f"Default value must be JSON serializable, got: {value}")

    @model_validator(mode="after")
    def _kind_structure(self) -> Self:
        port: Any = self
        kind = _value(port.kind)
        children = port.children
        if kind == "STRUCTURE" and port.identifier is None:
            raise ValueError("When specifying a structure you need to provide an identifier")
        if kind == "ENUM" and not port.choices:
            raise ValueError("When specifying an enum you need to provide its 'choices'")
        if kind == "QUANTITY" and not port.reference_unit:
            raise ValueError(
                "When specifying a quantity you need to provide a 'reference_unit' "
                "(e.g. 'volt'). It is the default selection and other units of the same "
                "dimension are still allowed."
            )
        if kind == "LIST":
            if children is None:
                raise ValueError("When specifying a list you need to provide a wrapped 'children' port")
            if len(children) != 1:
                raise ValueError("A list has exactly one child (its item type)")
        if kind == "DICT":
            if not children:
                raise ValueError("When specifying a dict you need to provide 'children' ports")
            if len(children) > 1 and any(child.key == ITEM_KEY for child in children):
                raise ValueError(
                    f"A dict is either homogeneous (one child keyed {ITEM_KEY!r}) "
                    "or has named children, not both"
                )
        return self


class SliderRules(BaseModel):
    """A SLIDER widget: both bounds, in order."""

    @model_validator(mode="after")
    def _bounds(self) -> Self:
        widget: Any = self
        if widget.min is None or widget.max is None:
            raise ValueError("When specifying a Slider you need to provide a 'min' and a 'max'")
        if widget.min > widget.max:
            raise ValueError("When specifying a Slider, 'max' must not be less than 'min'")
        return self


class StateChoiceRules(BaseModel):
    """A STATE_CHOICE widget: exactly one pointer form."""

    @model_validator(mode="after")
    def _one_pointer(self) -> Self:
        widget: Any = self
        if (widget.state_path is None) == (widget.state_call is None):
            raise ValueError("STATE_CHOICE widget needs exactly one of state_path or state_call")
        return self


class ValidatorRules(BaseModel):
    """A port validator: a pure call over the port and its declared dependencies."""

    @model_validator(mode="after")
    def _pure(self) -> Self:
        validator: Any = self
        check_pure_call(
            validator.call,
            validator.dependencies,
            f"Validator {validator.label or validator.call.operation}",
        )
        return self


class FitsRules(BaseModel):
    """A ``Fits``: a formula a database predicate can express."""

    @model_validator(mode="after")
    def _predicate(self) -> Self:
        fits: Any = self
        check_fits(fits.call, f"Fits of {fits.port}")
        return self


class ImplementationRules(BaseModel):
    """An implementation: its formulas read the arguments of its own definition."""

    @model_validator(mode="after")
    def _fits_single_structures(self) -> Self:
        implementation: Any = self
        ports = {port.key: port for port in implementation.definition.args or ()}
        seen: set[str] = set()
        for fits in implementation.fits or ():
            owner = f"The Fits of {implementation.interface or implementation.definition.name} on {fits.port}"
            port = ports.get(fits.port)
            if port is None:
                raise ValueError(f"{owner}: the action has no such argument")
            if _value(port.kind) != "STRUCTURE":
                raise ValueError(f"{owner}: only a single structure argument can have one")
            if fits.port in seen:
                raise ValueError(f"{owner}: an argument has one Fits; join conditions with and/or")
            seen.add(fits.port)
        return self

    @model_validator(mode="after")
    def _estimate_reads_its_arguments(self) -> Self:
        implementation: Any = self
        estimate = implementation.estimated_runtime
        if estimate is not None:
            name = implementation.interface or implementation.definition.name
            check_estimated_runtime(estimate.call, implementation.definition.args, f"The estimated runtime of {name}")
            if estimate.ui_call is not None:
                check_estimated_runtime_ui(estimate.ui_call, implementation.definition.args, f"The form's estimated runtime of {name}")
        return self


class EffectRules(BaseModel):
    """A port effect: a pure call over the port and its declared dependencies."""

    @model_validator(mode="after")
    def _pure(self) -> Self:
        effect: Any = self
        check_pure_call(
            effect.call,
            effect.dependencies,
            f"Effect {_value(effect.kind)} ({effect.call.operation})",
        )
        return self


class DefinitionRules(BaseModel):
    """A definition: every widget, validator and effect dependency is a resolvable port path.

    Args, returns, nested children and port groups are walked; a dependency is
    resolved from the top-level args and returns. Inside a call the root ``value``
    names the port's own value, so a sibling port called ``value`` is shadowed there.
    """

    @model_validator(mode="after")
    def _dependencies_resolve(self) -> Self:
        definition: Any = self
        roots = [*(definition.args or ()), *(definition.returns or ())]

        def check(dependencies: Iterable[str] | None, owner: str) -> None:
            for dependency in dependencies or ():
                if not resolve_port_path(dependency, roots):
                    raise ValueError(f"{owner} has invalid dependency: {dependency}")

        def walk(ports: Iterable[Any], prefix: str = "") -> None:
            for port in ports:
                path = f"{prefix}{port.key}"
                widget = getattr(port, "widget", None)
                if widget is not None and _value(widget.kind) == "SEARCH" and widget.dependencies:
                    check(widget.dependencies, f"Search widget in port {path}")
                for validator in getattr(port, "validators", None) or ():
                    check(
                        validator.dependencies,
                        f"Validator {validator.label or validator.call.operation} in port {path}",
                    )
                for effect in port.effects or ():
                    check(
                        effect.dependencies,
                        f"Effect {_value(effect.kind)} ({effect.call.operation}) in port {path}",
                    )
                walk(port.children or (), f"{path}{PORT_PATH_SEPARATOR}")

        walk(definition.args or ())
        walk(definition.returns or ())
        for group in definition.port_groups or ():
            for effect in group.effects or ():
                check(
                    effect.dependencies,
                    f"Effect {_value(effect.kind)} ({effect.call.operation}) "
                    f"in port group {group.key}",
                )
        return self

    @model_validator(mode="after")
    def _protocols_are_names(self) -> Self:
        """A protocol hint is a name to filter on: one word, said once."""
        protocols: tuple[str, ...] = tuple(getattr(self, "protocols", None) or ())
        for protocol in protocols:
            # The server stores a hint as a name of at most 1000 characters.
            if len(protocol) > 1000 or protocol.split() != [protocol]:  # empty, or with whitespace
                raise ValueError(f"A protocol hint is a name without whitespace, got {protocol!r}")
        twice = sorted({protocol for protocol in protocols if protocols.count(protocol) > 1})
        if twice:
            raise ValueError(f"The protocol hint {', '.join(twice)} is given twice")
        return self


_CATALOG = re.compile(r"[a-z][a-z0-9_-]*@[0-9]+")
_FIELD_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*")
_LOCAL_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_GRAPHQL_COMMENT = re.compile(r"#[^\n]*")


def operation_keyword(document: str) -> str:
    """The kind of a GraphQL document's first operation: ``query`` (also for ``{ ... }``),
    ``mutation``, ``subscription``, or ``fragment`` when it starts with one."""
    text = _GRAPHQL_COMMENT.sub("", document).lstrip()
    if text.startswith("{"):
        return "query"
    match = re.match(r"[A-Za-z_]+", text)
    return match.group(0) if match else ""


def _unique(keys: list[str], what: str) -> None:
    from arkitekt_spec.blok.vocabulary import BlokError

    twice = next((key for key in keys if keys.count(key) > 1), None)
    if twice is not None:
        raise BlokError("manifest.duplicate", what=what, key=twice)


class _Keyed(Protocol):
    key: str


class _Document(Protocol):
    key: str
    document: str


class _Query(_Document, Protocol):
    more: str | None


class _Subscription(_Document, Protocol):
    history: str | None


class _Demo(Protocol):
    deps: object


class _BlokDeclarations(Protocol):
    """What :func:`check_blok_declarations` reads of a v2 blok (agent or action blok)."""

    components: object
    source: str | None
    catalogs: tuple[str, ...]
    locals: Sequence[_Keyed]
    queries: Sequence[_Query]
    subscriptions: Sequence[_Subscription]
    demo: _Demo | None


def check_blok_declarations(blok: _BlokDeclarations, kind: str) -> None:
    """The server's ``check_blok_manifest`` for what a v2 blok declares beside its tree.

    Sent one way; its catalogs spelled ``name@version``; its locals, queries and subscriptions
    each named once, by a name no root reserves; a query a ``query``, a subscription a
    ``subscription``, neither a mutation; a subscription's history a declared query; and,
    for an action logo, nothing but the task it is drawn against.

    Raises:
        ValueError: On the first rule broken.
    """
    from arkitekt_spec.blok.vocabulary import BlokError, load_vocabulary

    if blok.components is not None and blok.source is not None:
        raise BlokError("manifest.sent", problem="not both")
    if blok.components is None and blok.source is None:
        raise BlokError("manifest.sent", problem="and this one has neither")
    reserved = load_vocabulary().reserved
    for catalog in blok.catalogs or ():
        if not _CATALOG.fullmatch(catalog):
            raise BlokError("manifest.catalog", catalog=catalog)
    named: tuple[tuple[str, Sequence[_Keyed]], ...] = (("locals", blok.locals), ("queries", blok.queries), ("subscriptions", blok.subscriptions))
    for what, entries in named:
        keys = [entry.key for entry in entries]
        _unique(keys, what)
        for key in keys:
            if not _LOCAL_NAME.fullmatch(key):
                raise BlokError("manifest.name", what=what, key=key)
            if what == "locals" and key in reserved:
                raise BlokError("roots.reserved", name=key)
    for query in blok.queries or ():
        _check_operation("query", query.key, operation_keyword(query.document))
        if query.more is not None and not _FIELD_PATH.fullmatch(query.more):
            raise BlokError("query.more", name=query.key, more=query.more, problem="is not a field path into its data (positions, images.items)")
    declared = {query.key for query in blok.queries or ()}
    for subscription in blok.subscriptions or ():
        _check_operation("subscription", subscription.key, operation_keyword(subscription.document))
        if subscription.history is not None and subscription.history not in declared:
            raise BlokError("query.history", name=subscription.key, history=subscription.history)
    dependencies: Sequence[_Keyed] = getattr(blok, "dependencies", None) or ()
    if kind == "action_logo" and (blok.locals or blok.queries or blok.subscriptions or dependencies):
        raise BlokError("manifest.action_logo")
    demo = blok.demo
    deps = demo.deps if demo is not None else None
    if isinstance(deps, Mapping):
        allowed = {dependency.key for dependency in dependencies} | {"self"}
        given: Mapping[str, object] = cast("Mapping[str, object]", deps)
        unknown = sorted(set(given) - allowed)
        if unknown:
            raise BlokError("manifest.demo", keys=", ".join(unknown))


def _check_operation(kind: str, name: str, operation: str) -> None:
    """A query's document is a ``query``, a subscription's a ``subscription``; neither writes."""
    from arkitekt_spec.blok.vocabulary import BlokError

    if operation == kind:
        return
    if operation == "mutation":
        raise BlokError("query.read_only", kind=kind, name=name, problem="is a mutation: a blok reads, and writes only through a task")
    raise BlokError("query.kind", kind=kind, name=name, operation=operation or "document without an operation")


def _wrong_context(kind: str, context: object) -> ValueError:
    """``context.kind``: a blok of ``kind`` drawn in a context its kind has no blok for."""
    from arkitekt_spec.blok.vocabulary import BlokError, load_vocabulary

    contexts = load_vocabulary().kind(kind).contexts
    return BlokError("context.kind", n="n" if kind[:1] in "aeiou" else "", kind=kind, contexts=", ".join(contexts), context=str(context))


class BlokRules(BaseModel):
    """An agent's blok: sent as its tree or its source, its declarations well-formed."""

    @model_validator(mode="after")
    def _manifest(self) -> Self:
        blok: Any = self
        context = _value(blok.context)
        if context == "OVERLAY":
            raise _wrong_context("agent", context)
        check_blok_declarations(blok, "action_logo" if context == "ACTION_LOGO" else "agent")
        _check_blok_args(self)
        return self


class ActionBlokRules(BaseModel):
    """An action's blok: sent one way, in a context an action has a blok for."""

    @model_validator(mode="after")
    def _manifest(self) -> Self:
        blok: Any = self
        context = _value(blok.context)
        if context not in ("HTML", "SCENE", "OVERLAY"):
            raise _wrong_context("action", context)
        check_blok_declarations(blok, "action")
        return self


class AskBlokRules(BaseModel):
    """An action's declared ask: an html task blok, its args and returns each keyed once."""

    @model_validator(mode="after")
    def _manifest(self) -> Self:
        blok: Any = self
        check_blok_declarations(blok, "task")
        for what, ports in (("args", blok.args), ("returns", blok.returns)):
            keys = [port.key for port in ports]
            twice = next((key for key in keys if keys.count(key) > 1), None)
            if twice is not None:
                raise ValueError(f"Ask {blok.key!r}: duplicate {what} key {twice!r}")
        return self


class InformBlokRules(BaseModel):
    """An action's declared inform: an html task blok, its args each keyed once."""

    @model_validator(mode="after")
    def _manifest(self) -> Self:
        blok: Any = self
        check_blok_declarations(blok, "task")
        keys = [port.key for port in blok.args]
        twice = next((key for key in keys if keys.count(key) > 1), None)
        if twice is not None:
            raise ValueError(f"Inform {blok.key!r}: duplicate args key {twice!r}")
        return self


def _check_blok_args(rules: BaseModel) -> None:
    """The server's ``check_blok_args``: an html blok's args, keyed once, owning the ``args`` root."""
    blok: Any = rules
    if not blok.args:
        return
    context = getattr(blok.context, "value", blok.context)
    if context != "HTML":
        raise ValueError(f"Blok manifest: only an html blok takes args, not a {context} blok")
    keys = [port.key for port in blok.args]
    twice = next((key for key in keys if keys.count(key) > 1), None)
    if twice is not None:
        raise ValueError(f"Blok manifest args: duplicate key {twice!r}")


class AgentRules(BaseModel):
    """A whole agent declaration: unique interfaces, and every referenced lock provided."""

    @model_validator(mode="after")
    def _invariants(self) -> Self:
        agent: Any = self
        implementations: tuple[Any, ...] = tuple(agent.implementations or ())
        states: tuple[Any, ...] = tuple(agent.states or ())
        _ensure_unique(
            (i.interface or i.definition.name for i in implementations),
            "implementation interface",
        )
        _ensure_unique((state.interface for state in states), "state interface")
        lock_keys = {lock.key for lock in agent.locks or ()}
        for implementation in implementations:
            name = implementation.interface or implementation.definition.name
            locks: tuple[str, ...] = tuple(implementation.locks or ())
            for lock in locks:
                if lock not in lock_keys:
                    raise ValueError(
                        f"Implementation '{name}' references lock '{lock}' that is "
                        f"not provided. Available locks: {sorted(lock_keys)}"
                    )
        return self
