"""The structural rules of the action language that need nothing but the models.

Mixed into the models of :mod:`arkitekt_spec.actions`, so they run wherever a
declaration is read -- rekuest building one, the CLI recording one, a server
ingesting one. Checks that need more than the models (parsing a search query,
resolving a state reference, validating a blok component) are the consumer's.

This module never imports :mod:`arkitekt_spec.actions` at runtime (the models
inherit from it), so enum-valued fields are compared by value: the models store
them as plain strings (``use_enum_values=True``).
"""

from collections.abc import Iterable, Iterator, Mapping
from typing import Any, Protocol, Self

from pydantic import BaseModel, field_validator, model_validator

#: The reserved ``value_path`` root that refers to the port's own value.
OWN_VALUE = "value"

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


def resolve_port_path(path: str, ports: Iterable[Any]) -> bool:
    """True if a port path (``a..b..c``) resolves through ``children`` from the given roots."""
    candidates = list(ports)
    for segment in path.split(PORT_PATH_SEPARATOR):
        match = next((port for port in candidates if port.key == segment), None)
        if match is None:
            return False
        candidates = list(match.children or ())
    return True


def check_demo_state(dependencies: Iterable[Any] | None, demo_state: Any) -> None:  # noqa: ANN401
    """A blok's ``demo_state`` names exactly the states each dependency references."""
    if not isinstance(demo_state, Mapping):
        return
    states: Mapping[str, Any] = demo_state  # pyright: ignore[reportUnknownVariableType]
    for dependency in dependencies or ():
        if dependency.key not in states:
            continue
        demo: Mapping[str, Any] = states[dependency.key]
        state_keys = {demand.key for demand in dependency.state_dependencies or ()}
        for state_key in state_keys:
            if state_key not in demo:
                raise ValueError(
                    f"demo_state for '{dependency.key}' missing key '{state_key}' referenced in blok"
                )
        extra = set(demo.keys()) - state_keys
        if extra:
            raise ValueError(
                f"demo_state for '{dependency.key}' has extra keys not referenced in blok: "
                f"{sorted(extra)}"
            )


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


class BlokRules(BaseModel):
    """A blok implementation: its demo state matches its dependencies' state demands."""

    @model_validator(mode="after")
    def _demo_state(self) -> Self:
        blok: Any = self
        check_demo_state(blok.dependencies, blok.demo_state)
        return self


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
