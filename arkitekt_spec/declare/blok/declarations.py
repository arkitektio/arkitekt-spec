"""What a v2 blok declares beside its tree, as an app writes it.

::

    app.blok(
        "deconvolver",
        BLOK,
        locals={"iterations": Local(15, persist=True), "image": Local[Image | None]()},
        queries={
            "positions": Query(
                service="mikro",
                document="query($slide: ID!) { positions(slide: $slide) { id name x y z } }",
                variables={"slide": "args.slide.id"},
            ),
        },
        demo={"args": {"slide": {"id": "1"}}},
    )

A variable is an expression over ``args``, locals and agents (``"args.slide.id"``); a value that
is not a string is a literal, and a literal string is written as one (``'"latest"'``). The
viewed moment is the root ``then`` (null while live): ``variables={"at": "then"}``.

A local is a port, typed as an action's argument is, by the first of:

1. ``Local[T]()`` or ``Local(type=T)``, through the same conversion as an argument's
   annotation (structures included);
2. ``Local.like(action, "arg")``: that argument's port, borrowed;
3. its default (``Local(15)`` is an INT, ``Local(["a"])`` a ``list[str]``);
4. its use in the tree: the task argument it is passed to whole, else the control bound to it
   (:mod:`arkitekt_spec.declare.blok.infer`);
5. else ``ANY``, an untyped value.
"""

import functools
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Generic, Protocol, TypeVar, cast

from arkitekt_spec.actions import (
    ActionArgumentInput,
    BlokDemoInput,
    BlokLocalInput,
    BlokQueryInput,
    BlokSubscriptionInput,
)
from arkitekt_spec.blok.bsx import parse_argument
from arkitekt_spec.blok.vocabulary import BlokError
from arkitekt_spec.scalars import JSONSerializable

if TYPE_CHECKING:
    from arkitekt_spec.declare.app import AppRegistry

Variable = str | int | float | bool | None
"""A query variable: an expression (a string), or a literal."""

T = TypeVar("T")


@dataclass(frozen=True)
class Borrow:
    """What :meth:`Local.like` borrows: an action's argument, by the action's interface."""

    interface: str
    argument: str
    own_default: bool
    """Whether the local's own default replaces the argument's."""


class _ArgDefault:
    """The default of :meth:`Local.like` when none is given: the argument's own."""

    def __repr__(self) -> str:
        return "<the argument's default>"


ARG_DEFAULT = _ArgDefault()



class Interfaced(Protocol):
    """What :meth:`Local.like` reads of an action: its interface. Every registered action
    (``@app.register``, a :class:`~arkitekt_spec.declare.register.WrappedFunction` of any
    signature) is one."""

    @property
    def interface(self) -> str: ...

@dataclass(frozen=True)
class Local(Generic[T]):
    """A local: form state that feeds a call, written only through ``bind`` on a control.

    A local is a port: ``Local(15)`` is an INT starting at 15, ``Local[float]()`` a FLOAT with no
    default, ``Local[Image | None]()`` a nullable structure, ``Local(type=list[str])`` a list.

    Args:
        default: The value it starts with; its kind when no type is given.
        type: What it holds, as an annotation (``int``, ``list[str]``, a registered structure);
            what ``Local[T]`` gives.
        persist: Remember the last input on this device.
        description: What it is for.
        label: How a control names it.
        borrow: The action argument whose port it is (:meth:`like`).
    """

    default: T | None = None
    type: object = field(default=None, kw_only=True)
    persist: bool = field(default=False, kw_only=True)
    description: str | None = field(default=None, kw_only=True)
    label: str | None = field(default=None, kw_only=True)
    borrow: Borrow | None = field(default=None, kw_only=True)

    @classmethod
    def like(
        cls,
        action: "Interfaced | str",
        argument: str,
        *,
        default: object = ARG_DEFAULT,
        persist: bool = False,
        description: str | None = None,
        label: str | None = None,
    ) -> "Local[object]":
        """A local whose port is an action argument's: its kind, identifier, children, choices,
        units, widget, label and description (validators and effects stay the action's).

        The action is resolved through the app's registered actions when the blok is registered
        (``local.like`` when it is none of them, or has no such argument), so register it
        first; ``action`` is the registered function or its interface.

        Args:
            action: The registered action (``@app.register`` function) or its interface.
            argument: The argument's key.
            default: The local's start value; the argument's default when left out.
            persist: Remember the last input on this device.
            description: Replaces the argument's description.
            label: Replaces the argument's label.
        """
        interface = action if isinstance(action, str) else action.interface
        own = not isinstance(default, _ArgDefault)
        return Local(
            default if own else None,
            persist=persist,
            description=description,
            label=label,
            borrow=Borrow(interface=interface, argument=argument, own_default=own),
        )

    def __post_init__(self) -> None:
        """Take the type ``Local[T]`` gave, when none is passed."""
        given: object = getattr(type(self), "__local_type__", None)
        if self.type is None and given is not None:
            object.__setattr__(self, "type", given)

    def __class_getitem__(cls, item: object) -> "type[Local[T]]":  # pyright: ignore[reportIncompatibleMethodOverride]
        """``Local[T]``: a local typed ``T`` (``typing`` would drop the parameter on a frozen instance)."""
        try:
            typed = _typed_local(item)
        except TypeError:  # an unhashable annotation (Annotated with a dict marker): not cached
            typed = _typed_local.__wrapped__(item)
        return cast("type[Local[T]]", typed)


@functools.cache
def _typed_local(item: object) -> "type[Local[object]]":
    return type(f"Local[{getattr(item, '__name__', repr(item))}]", (Local,), {"__local_type__": item, "__slots__": ()})


def _scalar_type(value: object) -> type | None:
    for candidate in (bool, int, float, str):
        if type(value) is candidate:
            return candidate
    return None


def infer_local_type(default: object) -> object | None:
    """The annotation a local's default implies: ``15`` an ``int``, ``["a"]`` a ``list[str]``;
    None when it says nothing of its kind (``None``, ``[]``, ``{}``, mixed items), which leaves
    the kind to the tree."""
    scalar = _scalar_type(default)
    if scalar is not None:
        return scalar
    if isinstance(default, list):
        items = {_scalar_type(item) for item in default}  # pyright: ignore[reportUnknownVariableType]
        if len(items) == 1 and None not in items:
            item = items.pop()
            return list[item]  # pyright: ignore[reportInvalidTypeArguments, reportGeneralTypeIssues]
        return None
    if isinstance(default, dict):
        values = {_scalar_type(value) for value in default.values()}  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
        if len(values) == 1 and None not in values:
            value = values.pop()
            return dict[str, value]  # pyright: ignore[reportInvalidTypeArguments, reportGeneralTypeIssues]
        return None
    return None


@dataclass(frozen=True)
class Query:
    """A declared read, ``queries.<name>``: one GraphQL query on a service.

    Args:
        service: The service it runs on, by key (``"mikro"``).
        document: The query. Never a mutation.
        variables: Each an expression (``"args.slide.id"``) or a literal; re-run when one changes.
        poll: Also re-run every this many seconds.
        more: The list field of its result new pages append to (``"positions"``, a dotted path
            into its data), which ``queries.<name>.fetch_more(...)`` needs.
    """

    service: str
    document: str
    variables: Mapping[str, Variable] = field(default_factory=dict[str, Variable])
    poll: float | None = None
    more: str | None = None


@dataclass(frozen=True)
class Subscription:
    """A declared stream, ``subscriptions.<name>``: one GraphQL subscription on a service.

    Args:
        keep: Keep the last this many payloads as a list; the latest only when left out.
        history: A declared query to replay from while travelling.
    """

    service: str
    document: str
    variables: Mapping[str, Variable] = field(default_factory=dict[str, Variable])
    keep: int | None = None
    history: str | None = None


def variable_inputs(variables: Mapping[str, Variable]) -> tuple[ActionArgumentInput, ...]:
    """Variables as they are stored: an expression parsed, a literal as it is."""
    stored: list[ActionArgumentInput] = []
    for key, value in variables.items():
        if isinstance(value, str):
            stored.append(parse_argument(key, value))
        elif value is None:
            stored.append(ActionArgumentInput(key=key))
        else:
            stored.append(ActionArgumentInput(key=key, value_literal=value))
    return tuple(stored)


BORROW_EXCLUDED = frozenset({"key", "default", "validators", "effects"})
"""What :meth:`Local.like` leaves of the argument's port: validators and effects name the
action's other arguments."""


def _borrowed(key: str, local: "Local[object]", borrow: Borrow, app_registry: "AppRegistry") -> BlokLocalInput:
    implementation = app_registry.implementations.get(borrow.interface)
    if implementation is None:
        raise BlokError("local.like", name=key, action=borrow.interface, problem=f"is no action this app registered (registered: {', '.join(sorted(app_registry.implementations)) or 'none'}); register it before the blok")
    ports = implementation.definition.args or ()
    port = next((port for port in ports if port.key == borrow.argument), None)
    if port is None:
        raise BlokError("local.like", name=key, action=borrow.interface, problem=f"has no argument {borrow.argument!r} (its arguments: {', '.join(p.key for p in ports) or 'none'})")
    borrowed: dict[str, object] = {**port.model_dump(exclude=set(BORROW_EXCLUDED), exclude_none=True), "key": key, "persist": local.persist}
    default = local.default if borrow.own_default else port.default
    if default is not None:
        borrowed["default"] = default
    if local.description is not None:
        borrowed["description"] = local.description
    if local.label is not None:
        borrowed["label"] = local.label
    return BlokLocalInput.model_validate(borrowed)


def local_input(key: str, local: "Local[object]", app_registry: "AppRegistry") -> BlokLocalInput:
    """One local as the wire carries it: its port by the first rule of the module that types
    it (a type, a borrowed argument, its default), plus ``persist``. When none does, its kind is
    left out: :func:`~arkitekt_spec.declare.blok.infer.resolve_scope` takes it from the tree,
    ``ANY`` when the tree does not tell either.

    Raises:
        BlokError: ``local.kind``, when ``type`` is a name rather than an annotation;
            ``local.like``, when the borrowed action or argument is not this app's.
    """
    from arkitekt_spec.declare.definition.define import convert_object_to_argport

    if isinstance(local.type, str):
        raise BlokError("local.kind", name=key, type=local.type)
    if local.type is None and local.borrow is not None:
        return _borrowed(key, local, local.borrow, app_registry)
    annotation = local.type if local.type is not None else infer_local_type(local.default)
    if annotation is None:
        untyped: dict[str, object] = {"key": key, "persist": local.persist, "default": local.default, "description": local.description, "label": local.label}
        return BlokLocalInput.model_validate({name: value for name, value in untyped.items() if value is not None})
    port = convert_object_to_argport(annotation, key, app_registry.structure_registry, default=local.default, description=local.description, label=local.label)
    return BlokLocalInput.model_validate({**port.model_dump(), "persist": local.persist})


def local_inputs(
    locals_: "Mapping[str, Local[object] | JSONSerializable] | None", app_registry: "AppRegistry"
) -> tuple[BlokLocalInput, ...]:
    """``locals`` as the wire carries them; a bare value is a local with that default."""
    stored: list[BlokLocalInput] = []
    for key, value in (locals_ or {}).items():
        local: Local[object] = value if isinstance(value, Local) else Local(value)  # pyright: ignore[reportUnknownVariableType]
        stored.append(local_input(key, local, app_registry))
    return tuple(stored)


def query_inputs(queries: "Mapping[str, Query] | None") -> tuple[BlokQueryInput, ...]:
    """``queries`` as the wire carries them."""
    return tuple(
        BlokQueryInput(
            key=key,
            service=query.service,
            document=query.document,
            variables=variable_inputs(query.variables),
            poll=query.poll,
            more=query.more,
        )
        for key, query in (queries or {}).items()
    )


def subscription_inputs(subscriptions: "Mapping[str, Subscription] | None") -> tuple[BlokSubscriptionInput, ...]:
    """``subscriptions`` as the wire carries them."""
    return tuple(
        BlokSubscriptionInput(
            key=key,
            service=subscription.service,
            document=subscription.document,
            variables=variable_inputs(subscription.variables),
            keep=subscription.keep,
            history=subscription.history,
        )
        for key, subscription in (subscriptions or {}).items()
    )


def demo_input(demo: "BlokDemoInput | Mapping[str, JSONSerializable] | None") -> BlokDemoInput | None:
    """``demo`` as the wire carries it: ``{deps, args, task, queries, subscriptions}``."""
    if demo is None or isinstance(demo, BlokDemoInput):
        return demo
    return BlokDemoInput.model_validate(dict(demo))


__all__ = [
    "ARG_DEFAULT",
    "Borrow",
    "Interfaced",
    "Local",
    "Query",
    "Subscription",
    "Variable",
    "demo_input",
    "infer_local_type",
    "local_input",
    "local_inputs",
    "query_inputs",
    "subscription_inputs",
    "variable_inputs",
]
