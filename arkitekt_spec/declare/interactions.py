"""Declared interactions: the bloks an action asks a person through, or shows them, typed like an action.

An interactions protocol is a class whose methods are each one ask or one inform, decorated
with its blok::

    class Picked(NamedTuple):
        cluster: ArrayLens
        other: ArrayLens

    @app.interactions
    class ClusterUI(Protocol):
        @ask(CHANNELS_BSX, locals={"cluster": Local[ArrayLens]()})
        def pick_channels(self, dataset: ChannelImage, error: str = "") -> Picked:
            \"\"\"Pick the two channels.\"\"\"

        @inform(PROGRESS_BSX)
        def progress(self, scene: Scene, step: int, total: int) -> None:
            \"\"\"Straightening progress.\"\"\"

    @app.workflow
    def segment(dataset: ChannelImage, ui: ClusterUI) -> ...:
        picked = ui.pick_channels(dataset=dataset)   # a Picked: the task holds until answered
        ui.progress(scene=scene, step=1, total=3)    # shown; the task goes on

The method is never run: its parameters are the blok's args (``args.<key>``), built as an
action's ports are and shrunk as a call's are. An ask's return is what an answer holds
(``task.answer(values: {...})``), expanded as a call's returns are; a dismissal raises
:class:`~arkitekt_spec.declare.errors.DismissedError`. An inform returns nothing and answers
nothing. The docstring is the message it is listed with.

A parameter annotated with the protocol is handed a proxy for the task the action runs in; the
action registers each method as one of its asks or informs, keyed ``<parameter>.<method>``. Its
tree is checked here, once, as the server checks it at registration: a task blok, its args the
method's parameters, every ``task.answer`` of an ask keyed by its returns, and none in an inform.
"""

import inspect
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from typing import TYPE_CHECKING, Any, ClassVar, Concatenate, Generic, Literal, ParamSpec, TypeVar, overload

from pydantic import BaseModel

from arkitekt_spec.actions import (
    AgentDependencyInput,
    AskBlokInput,
    BlokDemoInput,
    ComponentContext,
    ComponentNodeInput,
    DefinitionInput,
    InformBlokInput,
    UtilCallInput,
)
from arkitekt_spec.blok.bsx import parse_bsx
from arkitekt_spec.blok.vocabulary import BlokError, load_vocabulary
from arkitekt_spec.declare.blok.declarations import (
    Local,
    Query,
    Subscription,
    demo_input,
    local_inputs,
    query_inputs,
    subscription_inputs,
)
from arkitekt_spec.declare.blok.infer import resolve_scope
from arkitekt_spec.declare.blok.origin import declared_at, finding_key, warn_at
from arkitekt_spec.declare.blok.registry import catalogs_of, check_unknown_module
from arkitekt_spec.declare.blok.validate import BlokScope, blok_context, check_blok_tree
from arkitekt_spec.declare.catalogs import BlokCheckMode
from arkitekt_spec.declare.definition.define import prepare_definition
from arkitekt_spec.declare.definition.errors import DefinitionError
from arkitekt_spec.declare.definition.utils import interface_name
from arkitekt_spec.scalars import JSONSerializable

if TYPE_CHECKING:
    from arkitekt_spec.declare.app import AppRegistry

P = ParamSpec("P")
R = TypeVar("R")

ANSWER = "task.answer"
"""The call a task blok answers with."""

Role = Literal["ask", "inform"]
"""Whether the task holds on the blok until it is answered, or shows it and goes on."""


@dataclass(frozen=True)
class InteractionOptions:
    """What ``@ask`` and ``@inform`` declare beside the method: the blok and what it declares.

    Args:
        component: The tree, as v2 BSX (sent as such) or already parsed; an ``<Html>``.
        locals: Its form state, written only through ``bind``: each a :class:`Local`, or a bare default.
        dependencies: The agents it reads or calls besides ``self``, as ``deps.<key>``: each a
            protocol class this app declared (``@app.declare``).
        demo: What a preview renders it with.
        queries: Its declared reads.
        subscriptions: Its declared streams.
        catalogs: The catalogs it needs; those its components come from when left out.
        module: The module it renders against; the Orkestrator's when left out.
        description: What it asks or shows, in a sentence; the method's docstring when left out.
        mode: ``"strict"`` refuses on a catalog finding as well as on an error.
    """

    component: str | ComponentNodeInput
    locals: "Mapping[str, Local[object] | JSONSerializable] | None" = None
    dependencies: Mapping[str, type[Any]] | None = None
    demo: "BlokDemoInput | Mapping[str, JSONSerializable] | None" = None
    queries: "Mapping[str, Query] | None" = None
    subscriptions: "Mapping[str, Subscription] | None" = None
    catalogs: Sequence[str] | None = None
    module: str | None = None
    description: str | None = None
    mode: BlokCheckMode = "warning"


class BoundInteraction(Generic[P, R]):
    """What a type checker sees an ask or an inform as on an instance of its protocol: called
    with the method's arguments, an ask's answer back. The proxy a task is handed is the runtime's."""

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        """Ask and wait for the answer, or show the blok."""
        raise TypeError("An interactions protocol is never instantiated: annotate an action's parameter with it.")


class InteractionMethod(Generic[P, R]):
    """One ``@ask`` or ``@inform`` method of an interactions protocol: the placeholder and its blok."""

    role: ClassVar[Role]

    def __init__(self, func: Callable[..., Any], options: InteractionOptions) -> None:
        self.func = func
        self.options = options
        self.__name__ = func.__name__
        self.__doc__ = func.__doc__

    @overload
    def __get__(self, obj: None, owner: type[Any]) -> "InteractionMethod[P, R]": ...

    @overload
    def __get__(self, obj: object, owner: type[Any]) -> BoundInteraction[P, R]: ...

    def __get__(self, obj: object | None, owner: type[Any]) -> "InteractionMethod[P, R] | BoundInteraction[P, R]":
        if obj is None:
            return self
        return BoundInteraction()


class AskMethod(InteractionMethod[P, R]):
    """An ``@ask`` method: the task holds on its blok until a person answers."""

    role = "ask"


class InformMethod(InteractionMethod[P, R]):
    """An ``@inform`` method: its blok is shown while the task goes on."""

    role = "inform"


def ask(
    component: str | ComponentNodeInput,
    *,
    locals: "Mapping[str, Local[object] | JSONSerializable] | None" = None,
    dependencies: Mapping[str, type[Any]] | None = None,
    demo: "BlokDemoInput | Mapping[str, JSONSerializable] | None" = None,
    queries: "Mapping[str, Query] | None" = None,
    subscriptions: "Mapping[str, Subscription] | None" = None,
    catalogs: Sequence[str] | None = None,
    module: str | None = None,
    description: str | None = None,
    mode: BlokCheckMode = "warning",
) -> Callable[[Callable[Concatenate[Any, P], R]], AskMethod[P, R]]:
    """Make a method of an interactions protocol an ask through ``component`` (see
    :class:`InteractionOptions`).

    The method's parameters (after ``self``) are the blok's args, its return what an answer
    holds: a ``NamedTuple`` keys it by field, a single value is ``return0`` unless ``Key``
    names it.
    """
    options = InteractionOptions(
        component=component,
        locals=locals,
        dependencies=dependencies,
        demo=demo,
        queries=queries,
        subscriptions=subscriptions,
        catalogs=catalogs,
        module=module,
        description=description,
        mode=mode,
    )

    def decorate(func: Callable[Concatenate[Any, P], R]) -> AskMethod[P, R]:
        return AskMethod(func, options)

    return decorate


def inform(
    component: str | ComponentNodeInput,
    *,
    locals: "Mapping[str, Local[object] | JSONSerializable] | None" = None,
    dependencies: Mapping[str, type[Any]] | None = None,
    demo: "BlokDemoInput | Mapping[str, JSONSerializable] | None" = None,
    queries: "Mapping[str, Query] | None" = None,
    subscriptions: "Mapping[str, Subscription] | None" = None,
    catalogs: Sequence[str] | None = None,
    module: str | None = None,
    description: str | None = None,
    mode: BlokCheckMode = "warning",
) -> Callable[[Callable[Concatenate[Any, P], R]], InformMethod[P, R]]:
    """Make a method of an interactions protocol an inform through ``component`` (see
    :class:`InteractionOptions`).

    The method's parameters (after ``self``) are the blok's args; it returns ``None``, as
    nothing waits on the blok.
    """
    options = InteractionOptions(
        component=component,
        locals=locals,
        dependencies=dependencies,
        demo=demo,
        queries=queries,
        subscriptions=subscriptions,
        catalogs=catalogs,
        module=module,
        description=description,
        mode=mode,
    )

    def decorate(func: Callable[Concatenate[Any, P], R]) -> InformMethod[P, R]:
        return InformMethod(func, options)

    return decorate


def _util_calls(value: object) -> Iterator[UtilCallInput]:
    """Every util call anywhere in ``value``, nested ones included."""
    if isinstance(value, UtilCallInput):
        yield value
    if isinstance(value, BaseModel):
        for name in type(value).model_fields:
            yield from _util_calls(getattr(value, name))
    elif isinstance(value, (tuple, list)):
        for each in value:  # pyright: ignore[reportUnknownVariableType]
            yield from _util_calls(each)


def check_answers(component: ComponentNodeInput, definition: DefinitionInput, owner: str) -> None:
    """Every ``task.answer(values: {...})`` of the tree keyed by the ask's returns, each
    non-nullable one answered. A ``values`` that is not written out as a dict is the
    server's to check when it comes.

    Raises:
        DefinitionError: On a key that is not a return, or an answer leaving one out.
    """
    returns = {port.key: port for port in definition.returns or ()}
    required = {key for key, port in returns.items() if not port.nullable}
    for call in _util_calls(component):
        if call.operation != ANSWER:
            continue
        values = next((arg for arg in call.arguments or () if arg.key == "values"), None)
        if values is None or values.value_dict is None:
            continue
        keys = {entry.key for entry in values.value_dict if entry.key is not None}
        unknown = sorted(keys - returns.keys())
        if unknown:
            raise DefinitionError(
                f"{owner}: task.answer answers {', '.join(repr(k) for k in unknown)}, which the ask "
                f"does not return (its returns: {', '.join(repr(k) for k in returns) or 'none'})"
            )
        missing = sorted(required - keys)
        if missing:
            raise DefinitionError(
                f"{owner}: a task.answer leaves out {', '.join(repr(k) for k in missing)}, which every answer "
                "holds: make the return Optional, or answer it"
            )


def check_no_answer(component: ComponentNodeInput, owner: str) -> None:
    """No ``task.answer`` anywhere in an inform's tree: nothing waits on it.

    Raises:
        DefinitionError: On one.
    """
    if any(call.operation == ANSWER for call in _util_calls(component)):
        raise DefinitionError(f"{owner}: an inform answers nothing, but its blok calls task.answer: make it an @ask")


class DeclaredInteraction:
    """One ask or inform of an interactions protocol as one app declared it: its ports built,
    its blok checked."""

    def __init__(self, method: InteractionMethod[Any, Any], owner: str, registry: "AppRegistry") -> None:
        self.method = method
        self.role: Role = method.role
        self.key = method.__name__
        self.is_async = inspect.iscoroutinefunction(method.func)
        self.definition: DefinitionInput = prepare_definition(
            method.func,
            omitfirst=1,  # the protocol's `self`
            structure_registry=registry.structure_registry,
        )
        options = method.options
        role = self.role
        where = f"{owner}.{self.key}"
        if role == "inform" and self.definition.returns:
            raise DefinitionError(f"{where}: an inform returns nothing (nothing answers it): annotate it `-> None`")
        self.description = options.description or self.definition.description or self.key
        self.dependencies: tuple[AgentDependencyInput, ...] = tuple(
            registry.structure_registry.protocol_for(cls).to_dependency_input(key)
            for key, cls in (options.dependencies or {}).items()
        )
        view = registry.blok_view(options.module)
        source = options.component if isinstance(options.component, str) else None
        try:
            component = parse_bsx(options.component, view=view) if source is not None else options.component
            context = blok_context(component)
        except BlokError as error:
            raise error.within(f"{where} ({role})") from None
        if context != ComponentContext.HTML:
            raise DefinitionError(f"{where}: an {role} is an <Html> blok, not <{component.component}>")
        try:
            locals_ = local_inputs(options.locals, registry)
        except BlokError as error:
            raise error.within(f"{where} ({role})") from None
        queries = query_inputs(options.queries)
        subscriptions = subscription_inputs(options.subscriptions)
        demo = demo_input(options.demo)
        catalogs = tuple(options.catalogs or ()) or catalogs_of(component, view)
        scope = BlokScope(
            kind="task",
            context=context.value,
            dependencies=self.dependencies,
            locals=locals_,
            queries=queries,
            subscriptions=subscriptions,
            args=tuple(port.key for port in self.definition.args or ()),
            catalogs=catalogs,
            demo=demo,
        )
        scope = resolve_scope(component, scope, view)
        at = declared_at()
        reported: set[str] = set()
        for finding in check_unknown_module(options.module, registry):
            if options.mode == "strict":
                raise BlokError.from_finding(finding)
            warn_at(f"{where} ({role}): {finding.message} [{finding.code}]", at, finding=finding, blok=where)
        for finding in check_blok_tree(component, scope, view):
            if finding.level == "error" or options.mode == "strict":
                at_path = f", {finding.path}" if finding.path else ""
                message = f"{where} ({role}{at_path}): {finding.message}"
                raise BlokError.from_finding(dataclass_replace(finding, path=None, message=message), node=finding.path)
            if finding_key(finding) not in reported:
                reported.add(finding_key(finding))
                warn_at(f"{where} ({role}): {finding.message} [{finding.code}]", at, finding=finding, blok=where)
        if role == "ask":
            check_answers(component, self.definition, where)
        else:
            check_no_answer(component, where)
        self._fields: dict[str, object] = {
            "description": self.description,
            "vocab": load_vocabulary().vocab,
            "args": self.definition.args or (),
        }
        if role == "ask":
            self._fields["returns"] = self.definition.returns or ()
        optional: dict[str, object] = {
            "components": None if source is not None else (component,),
            "source": source,
            "module": options.module,
            "dependencies": self.dependencies or None,
            "catalogs": catalogs or None,
            "locals": locals_ or None,
            "demo": demo,
            "queries": queries or None,
            "subscriptions": subscriptions or None,
        }
        self._fields.update({name: value for name, value in optional.items() if value is not None})

    def to_ask_input(self, parameter: str) -> AskBlokInput:
        """This ask as an action whose ``parameter`` is the protocol registers it."""
        return AskBlokInput.model_validate({"key": f"{parameter}.{self.key}", **self._fields})

    def to_inform_input(self, parameter: str) -> InformBlokInput:
        """This inform as an action whose ``parameter`` is the protocol registers it."""
        return InformBlokInput.model_validate({"key": f"{parameter}.{self.key}", **self._fields})


class DeclaredInteractions:
    """An interactions protocol as one app declared it (``@app.interactions``): every ``@ask``
    and ``@inform`` method.

    Kept by the app's structure registry under the class, so an action's parameter annotated
    with it is told apart from a port, and its asks and informs registered with the action.
    """

    def __init__(self, cls: type[Any], registry: "AppRegistry") -> None:
        self.cls = cls
        self.interface = interface_name(cls)
        self.interactions: dict[str, DeclaredInteraction] = {}
        for name, member in inspect.getmembers(cls):
            if isinstance(member, InteractionMethod):
                self.interactions[name] = DeclaredInteraction(member, cls.__qualname__, registry)  # pyright: ignore[reportUnknownArgumentType]
            elif not name.startswith("_") and callable(member):
                raise DefinitionError(
                    f"{cls.__qualname__}.{name} is a method without @ask or @inform: every public "
                    "method of an interactions protocol is one. Decorate it, or prefix it with `_`."
                )
        if not self.interactions:
            raise DefinitionError(f"{cls.__qualname__} declares no interactions: give it an @ask or @inform method.")

    def to_ask_inputs(self, parameter: str) -> list[AskBlokInput]:
        """Every ask, as an action whose ``parameter`` is the protocol registers them."""
        return [each.to_ask_input(parameter) for each in self.interactions.values() if each.role == "ask"]

    def to_inform_inputs(self, parameter: str) -> list[InformBlokInput]:
        """Every inform, as an action whose ``parameter`` is the protocol registers them."""
        return [each.to_inform_input(parameter) for each in self.interactions.values() if each.role == "inform"]


__all__ = [
    "AskMethod",
    "BoundInteraction",
    "DeclaredInteraction",
    "DeclaredInteractions",
    "InformMethod",
    "InteractionMethod",
    "InteractionOptions",
    "Role",
    "ask",
    "check_answers",
    "check_no_answer",
    "inform",
]
