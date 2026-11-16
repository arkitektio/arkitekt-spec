"""Resolve what a blok leaves to its tree: the kinds of its ``<local>`` declarations.

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

(A given ``kind`` and ``like="self.<action>.<arg>"`` come before all of these: they give the
kind; ``like`` is resolved only where the blok is mounted.) Only uses in the local's scope (its
parent's subtree) count. Every field the local sets wins over the inferred type, which only fills
what it leaves out; a candidate that makes no valid port with them is skipped. The other call sites and binds are then checked against the kind found
(``local.arg_kind``, ``local.bind_kind``), which ``ANY`` skips.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

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
from arkitekt_spec.blok.declared import LOCAL_COMPONENT, declared_local, local_like, local_problem
from arkitekt_spec.blok.vocabulary import JSON
from arkitekt_spec.declare.blok.walk import BIND_PROP, Where, action_key_for, iter_nodes, walk_tree

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


@dataclass(frozen=True)
class ResolvedLocal:
    """A ``<local>`` with its kind resolved, and why its ``like`` borrows nothing (None when it
    borrows, has none, or the blok is not mounted)."""

    local: BlokLocalInput
    like_problem: str | None = None


@dataclass
class _Uses:
    """Where a tree uses its locals whole (task arguments, binds), per local node id.

    A local's binding is its node id; a ``let`` or ``into`` name is bound to None, so a path
    whose root is bound to a string reads a local, and that local only (scoping is the walker's).
    """

    dependencies: Mapping[str, AgentDependencyInput]
    view: BlokView | None
    arg_ports: ArgPorts
    arguments: dict[str, list[dict[str, object]]] = field(default_factory=dict[str, list[dict[str, object]]])
    binds: dict[str, list[str]] = field(default_factory=dict[str, list[str]])

    def visit_component(self, node: ComponentNodeInput, ancestors: tuple[ComponentNodeInput, ...], scope: Mapping[str, str | None]) -> None:
        spec = self.view.component(node.component) if self.view is not None else None
        bind = spec.prop(BIND_PROP) if spec is not None else None
        for prop in node.props or ():
            path = prop.dynamic_value.path if prop.key == BIND_PROP and prop.dynamic_value is not None else None
            local = scope.get(path) if path is not None else None
            if bind is not None and local is not None:
                self.binds.setdefault(local, []).append(bind.kind)

    def visit_path(self, path: str, where: Where, scope: Mapping[str, str | None]) -> None:
        return None

    def visit_agent_call(self, call: AgentProbeInput, where: Where, scope: Mapping[str, str | None], into: str | None) -> None:
        action = action_key_for(call)
        for argument in call.arguments or ():
            path = argument.value_path
            local = scope.get(path) if path is not None else None
            if local is None or argument.key is None:
                continue
            typed = argument_type(call.dependency, action, argument.key, self.dependencies, self.arg_ports)
            if typed is not None:
                self.arguments.setdefault(local, []).append(typed)

    def visit_util_call(self, call: UtilCallInput, where: Where, scope: Mapping[str, str | None], top: bool) -> None:
        return None

    def bind_let(self, name: str, items: ComponentPropInput | None, where: Where, scope: Mapping[str, str | None]) -> None:
        return None

    def bind_into(self, name: str, call: AgentProbeInput) -> None:
        return None

    def bind_local(self, name: str, node: ComponentNodeInput, scope: Mapping[str, str | None]) -> str:
        return node.id


def argument_type(
    dependency: str, action: str, argument: str, dependencies: Mapping[str, AgentDependencyInput], arg_ports: ArgPorts
) -> dict[str, object] | None:
    """The type of an action's argument, as this side knows it: the full arg port the SDK has of
    its own actions, else the arg match of a dependency's demand; None when neither tells."""
    port = next((each for each in arg_ports.get(dependency, {}).get(action, ()) if each.key == argument), None)
    if port is not None:
        return port_type(port)
    declared = dependencies.get(dependency)
    demand = next((each.demand for each in (declared.action_dependencies or ()) if each.key == action), None) if declared is not None else None
    match = next((each for each in (demand.arg_matches or ()) if each.key == argument), None) if demand is not None else None
    return match_type(match) if match is not None else None


BORROW_EXCLUDED = frozenset({"key", "default", "validators", "effects"})
"""What ``like`` leaves of the argument's port: validators and effects name the action's other arguments."""


def borrowed_type(
    like: str, dependencies: Mapping[str, AgentDependencyInput], arg_ports: ArgPorts
) -> tuple[dict[str, object] | None, str | None]:
    """What ``like="self.<action>.<arg>"`` / ``"deps.<key>.<action>.<arg>"`` borrows: the
    argument's port (its default too, which the local's own replaces), or why it borrows nothing."""
    parts = like.split(".")
    if parts[:1] == ["self"] and len(parts) == 3:
        dependency, action, argument = "self", parts[1], parts[2]
    elif parts[:1] == ["deps"] and len(parts) == 4:
        dependency, action, argument = parts[1], parts[2], parts[3]
    else:
        return None, "is no argument: write self.<action>.<arg> or deps.<key>.<action>.<arg>"
    port = next((each for each in arg_ports.get(dependency, {}).get(action, ()) if each.key == argument), None)
    if port is not None:
        borrowed = port.model_dump(exclude=set(BORROW_EXCLUDED), exclude_none=True)
        if port.default is not None:
            borrowed["default"] = port.default
        return borrowed, None
    if dependency not in dependencies and dependency != "self":
        return None, f"names the dependency {dependency!r}, which the mount does not have ({', '.join(sorted(dependencies)) or 'none'})"
    known = arg_ports.get(dependency, {})
    declared = dependencies.get(dependency)
    actions = {each.key: each for each in (declared.action_dependencies or ())} if declared is not None else {}
    if action not in known and action not in actions:
        return None, f"names the action {action!r}, which {dependency} does not have ({', '.join(sorted({*known, *actions})) or 'none'})"
    typed = argument_type(dependency, action, argument, dependencies, arg_ports)
    if typed is None:
        return None, f"names the argument {argument!r}, which {dependency}.{action} does not have, or does not type"
    return typed, None


def resolve_local(local: BlokLocalInput, arguments: Sequence[Mapping[str, object]], binds: Sequence[str], borrowed: Mapping[str, object] | None = None) -> BlokLocalInput:
    """``local`` with its kind resolved by the rules of this module (a given kind is kept)."""
    if kind_given(local):
        return local
    candidates: list[Mapping[str, object]] = []
    if borrowed is not None:
        candidates.append(borrowed)
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


def resolve_locals(
    tree: ComponentNodeInput,
    dependencies: Sequence[AgentDependencyInput] | None = None,
    view: BlokView | None = None,
    *,
    arg_ports: ArgPorts | None = None,
) -> dict[str, ResolvedLocal]:
    """Every readable ``<local>`` of ``tree`` with its kind resolved (see the module), by node id.

    ``dependencies`` None is a blok not mounted: a ``like`` then borrows nothing, and says nothing
    of it either.
    """
    ports = arg_ports or {}
    by_key = {dependency.key: dependency for dependency in dependencies or ()}
    uses = _Uses(dependencies=by_key, view=view, arg_ports=ports)
    walk_tree(tree, uses)
    resolved: dict[str, ResolvedLocal] = {}
    for node in iter_nodes(tree):
        if node.component != LOCAL_COMPONENT or local_problem(node) is not None:
            continue
        local = declared_local(node)
        like = local_like(node)
        borrowed, problem = (None, None)
        if like is not None and (dependencies is not None or ports):
            borrowed, problem = borrowed_type(like, by_key, ports)
        resolved[node.id] = ResolvedLocal(
            local=resolve_local(local, uses.arguments.get(node.id, ()), uses.binds.get(node.id, ()), borrowed),
            like_problem=problem,
        )
    return resolved


__all__ = [
    "BORROW_EXCLUDED",
    "ArgPorts",
    "ResolvedLocal",
    "argument_type",
    "borrowed_type",
    "default_type",
    "kind_given",
    "match_type",
    "port_type",
    "resolve_local",
    "resolve_locals",
    "typed_local",
]
