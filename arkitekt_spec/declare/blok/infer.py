"""Resolve what a blok leaves to its tree: the kinds of its locals.

They are inferred the same way by the spec, the server and takt, so a source blok sent without
kinds resolves the same everywhere. Nothing else is inferred: every ``deps.<key>`` a blok reads,
whatever its kind, must be declared (``deps.unknown`` otherwise).

**A local's kind**, when it is left out (a given kind, ``ANY`` included, is kept), is the first
of:

1. its ``default``: a bool ``BOOL``, an integer ``INT``, a number ``FLOAT``, a string ``STRING``,
   a non-empty list of one scalar kind a ``LIST`` of it, a non-empty object of one scalar kind a
   ``DICT`` of it (the item port keyed ``...``);
2. the port of the task argument it is passed to whole (``self.move(to: position)``), at the
   first such call in tree order whose action port is known and makes a valid local (a demand's
   arg match with a kind; the SDK passes the action's full arg ports);
3. the bind kind of the first control bound to it whole (``<Slider bind={x}>``: ``FLOAT``), when
   that is ``STRING``, ``INT``, ``FLOAT`` or ``BOOL``;
4. else ``ANY``: an untyped value, never an error.

(The SDK's ``Local[T]()``, ``Local(type=T)`` and ``Local.like(...)`` come before all of these:
they give the kind.) Every field the local sets wins over the inferred type, which only fills
what it leaves out; a candidate that makes no valid port with them is skipped. The other call sites and binds are then checked against the kind found
(``local.arg_kind``, ``local.bind_kind``), which ``ANY`` skips.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from pydantic import ValidationError

from arkitekt_spec.actions import (
    LOCAL_ANY,
    AgentDependencyInput,
    AgentProbeInput,
    ArgPortInput,
    BlokLocalInput,
    ComponentNodeInput,
    ComponentPropInput,
    PortMatchInput,
    UtilCallInput,
)
from arkitekt_spec.blok.catalogs import BlokView
from arkitekt_spec.blok.vocabulary import JSON
from arkitekt_spec.declare.blok.walk import BIND_PROP, Where, action_key_for, walk_tree

if TYPE_CHECKING:
    from arkitekt_spec.declare.blok.validate import BlokScope

ArgPorts = Mapping[str, Mapping[str, Sequence[ArgPortInput]]]
"""Per dependency key (``self``), per action, its arg ports: what the SDK knows of its own actions."""

ITEM_KEY = "..."
BIND_INFERRED = ("STRING", "INT", "FLOAT", "BOOL")
"""The bind kinds a local takes from its control; a LIST, DICT or ANY control tells too little."""
TYPE_FIELDS = ("kind", "identifier", "nullable", "choices", "reference_unit", "proposed_units", "dimension", "children")
"""What a local takes of an action's arg port when it is inferred from one: its type, nothing else."""


def kind_given(local: BlokLocalInput) -> bool:
    """Whether ``local`` says its kind (``ANY`` included), rather than leaving it to the tree."""
    return "kind" in local.model_fields_set


def _scalar_kind(value: JSON | object) -> str | None:
    if isinstance(value, bool):
        return "BOOL"
    if isinstance(value, int):
        return "INT"
    if isinstance(value, float):
        return "FLOAT"
    if isinstance(value, str):
        return "STRING"
    return None


def default_type(default: object) -> dict[str, object] | None:
    """The type a default implies (rule 1), or None when it says nothing (``None``, ``[]``, ``{}``,
    mixed items)."""
    scalar = _scalar_kind(default)
    if scalar is not None:
        return {"kind": scalar}
    items: list[object] = list(default) if isinstance(default, list) else list(default.values()) if isinstance(default, dict) else []  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
    kinds = {_scalar_kind(item) for item in items}
    if len(kinds) != 1 or None in kinds:
        return None
    (item_kind,) = kinds
    return {"kind": "LIST" if isinstance(default, list) else "DICT", "children": [{"key": ITEM_KEY, "kind": item_kind}]}


def match_type(match: PortMatchInput) -> dict[str, object] | None:
    """The type an arg match says (a kind, recursively for its children), or None when it says
    too little to be a port."""
    if match.kind is None:
        return None
    typed: dict[str, object] = {"kind": match.kind, "identifier": match.identifier, "nullable": bool(match.nullable)}
    if match.children:
        children: list[dict[str, object]] = []
        for child in match.children:
            child_type = match_type(child)
            if child_type is None or child.key is None:
                return None
            children.append({**child_type, "key": child.key})
        typed["children"] = children
    return typed


def port_type(port: ArgPortInput) -> dict[str, object]:
    """The type of an action's arg port: :data:`TYPE_FIELDS`, nothing else."""
    dumped = port.model_dump(include=set(TYPE_FIELDS))
    return {key: value for key, value in dumped.items() if value is not None}


def typed_local(local: BlokLocalInput, typed: Mapping[str, object]) -> BlokLocalInput | None:
    """``local`` with the type ``typed`` filling what it does not say: every field the local
    sets (its key, default, choices, nullable, widget, ...) wins over the inferred one. None when
    that makes no valid local (a QUANTITY without a unit, an ENUM without choices, ...)."""
    own = local.model_dump(exclude={"kind"}, exclude_unset=True)
    try:
        return BlokLocalInput.model_validate({**typed, **own})
    except ValidationError:
        return None


def any_local(local: BlokLocalInput) -> BlokLocalInput:
    """``local`` resolved to ``ANY``."""
    return BlokLocalInput.model_validate({**local.model_dump(exclude_none=True, exclude={"kind"}), "kind": LOCAL_ANY})


@dataclass
class _Uses:
    """Where a tree uses its locals whole (task arguments, binds)."""

    locals: frozenset[str]
    dependencies: Mapping[str, AgentDependencyInput]
    view: BlokView | None
    arg_ports: ArgPorts
    arguments: dict[str, list[dict[str, object]]] = field(default_factory=dict[str, list[dict[str, object]]])
    binds: dict[str, list[str]] = field(default_factory=dict[str, list[str]])

    def visit_component(self, node: ComponentNodeInput, ancestors: tuple[ComponentNodeInput, ...], scope: Mapping[str, None]) -> None:
        spec = self.view.component(node.component) if self.view is not None else None
        bind = spec.prop(BIND_PROP) if spec is not None else None
        for prop in node.props or ():
            path = prop.dynamic_value.path if prop.key == BIND_PROP and prop.dynamic_value is not None else None
            if bind is not None and path is not None and path in self.locals and path not in scope:
                self.binds.setdefault(path, []).append(bind.kind)

    def visit_path(self, path: str, where: Where, scope: Mapping[str, None]) -> None:
        return None

    def visit_agent_call(self, call: AgentProbeInput, where: Where, scope: Mapping[str, None], into: str | None) -> None:
        action = action_key_for(call)
        ports = {port.key: port for port in self.arg_ports.get(call.dependency, {}).get(action, ())}
        dependency = self.dependencies.get(call.dependency)
        demand = next((each.demand for each in (dependency.action_dependencies or ()) if each.key == action), None) if dependency is not None else None
        matches = {match.key: match for match in (demand.arg_matches or ()) if match.key is not None} if demand is not None else {}
        for argument in call.arguments or ():
            path = argument.value_path
            if path is None or path not in self.locals or path in scope or argument.key is None:
                continue
            port = ports.get(argument.key)
            match = matches.get(argument.key)
            typed = port_type(port) if port is not None else match_type(match) if match is not None else None
            if typed is not None:
                self.arguments.setdefault(path, []).append(typed)

    def visit_util_call(self, call: UtilCallInput, where: Where, scope: Mapping[str, None], top: bool) -> None:
        return None

    def bind_let(self, name: str, items: ComponentPropInput | None, where: Where, scope: Mapping[str, None]) -> None:
        return None

    def bind_into(self, name: str, call: AgentProbeInput) -> None:
        return None


def _uses(tree: ComponentNodeInput, scope: "BlokScope", view: BlokView | None, arg_ports: ArgPorts) -> _Uses:
    uses = _Uses(
        locals=frozenset(local.key for local in scope.locals),
        dependencies={dependency.key: dependency for dependency in scope.dependencies},
        view=view,
        arg_ports=arg_ports,
    )
    walk_tree(tree, uses)
    return uses


def resolve_local(local: BlokLocalInput, arguments: Sequence[Mapping[str, object]], binds: Sequence[str]) -> BlokLocalInput:
    """``local`` with its kind resolved by the rules of this module (a given kind is kept)."""
    if kind_given(local):
        return local
    candidates: list[Mapping[str, object]] = []
    from_default = default_type(local.default)
    if from_default is not None:
        candidates.append(from_default)
    candidates.extend(arguments)
    candidates.extend({"kind": kind} for kind in binds if kind in BIND_INFERRED)
    for candidate in candidates:
        resolved = typed_local(local, candidate)
        if resolved is not None:
            return resolved
    return any_local(local)


def resolve_scope(
    tree: ComponentNodeInput,
    scope: "BlokScope",
    view: BlokView | None = None,
    *,
    arg_ports: ArgPorts | None = None,
) -> "BlokScope":
    """``scope`` with what ``tree`` tells: each local's kind (see the module). Resolving a
    resolved scope changes nothing."""
    uses = _uses(tree, scope, view, arg_ports or {})
    locals_ = tuple(resolve_local(local, uses.arguments.get(local.key, ()), uses.binds.get(local.key, ())) for local in scope.locals)
    return replace(scope, locals=locals_)


__all__ = ["ArgPorts", "default_type", "kind_given", "match_type", "port_type", "resolve_local", "resolve_scope", "typed_local"]
