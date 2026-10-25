"""An action's own bloks: how a client shows a run of it, declared inline with the action.

``@app.workflow(bloks=[ActionBlok(bsx("<Overlay>...</Overlay>"), context="overlay")])``: one
blok per context (``html`` for a task panel, ``overlay`` drawn into the space that hosts the
run), sent with the action's definition, never listed on its own, and not part of the action's
hash. It names devices only by the action's dependency keys -- the parameters annotated with a
protocol -- so a client binds them to the agents a run got. Checked here, at decoration, as the
server checks it at registration.
"""

import re
import warnings
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from arkitekt_spec.actions import (
    ACTION_BLOK_CONTEXTS,
    ACTION_BLOK_FILLED_ROOTS,
    ActionBlokInput,
    AgentProbeInput,
    BlokPersistInput,
    ComponentContext,
    ComponentNodeInput,
    UtilCallInput,
)
from arkitekt_spec.declare.blok.parser import bsx as parse_bsx
from arkitekt_spec.declare.blok.validate import check_context_root, validate_blok_catalog
from arkitekt_spec.declare.blok.walk import bound_calls, walk_component
from arkitekt_spec.declare.catalogs import BlokCheckMode, CatalogWarning
from arkitekt_spec.scalars import JSONSerializable

if TYPE_CHECKING:
    from arkitekt_spec.declare.app import AppRegistry

ACTION_BLOK_TASK_ROOT = "task"
"""The root an action's blok reads the run it is shown for under: ``@task.progress``, ``@task.ask``."""
ACTION_BLOK_SELF_KEY = "self"
"""The key an action's blok reads the agent running the action under: ``@self.<state>``."""
AT_COMPONENT = "At"
"""Anchors its children at the agent bound to ``dependency``, where it stands in the hosting space."""
ROUTE_COMPONENT = "Route"
"""With ``through``, a line through the agents bound to those dependencies."""
ACTION_BLOK_COMPONENTS = ("At", "Route", "TaskAsk")
"""The components that read a run or a dependency of one: only an action's own bloks hold them."""

_ACTION_BLOK_READS = frozenset({ACTION_BLOK_TASK_ROOT, "args", ACTION_BLOK_SELF_KEY, "$event", "utils"})


@dataclass(frozen=True)
class ActionBlok:
    """An action's own blok for one context, declared with the action (``bloks=[...]``).

    Args:
        component: The tree, as BSX (sent as such) or already parsed (``bsx(...)``).
        context: ``"html"`` for a task panel, ``"scene"`` for a 3D view of its own, or
            ``"overlay"`` drawn into the space that hosts the run: its root an ``<Overlay>``,
            whose ``<At dependency="...">`` anchors children at the agent the run got for that
            dependency.
        local_state: The blok's own locals and their initial values (``{"show_path": True}``).
        persist: The locals a renderer may keep between openings, on the device:
            ``["show_path"]``, or ``{"show_path": "Whether the route is drawn"}``.
        catalog: The UI catalog it renders against; the Orkestrator's when left out.
        description: What it shows of a run.

    The tree reads the action's dependency keys, ``@self`` (the agent running the action),
    ``@task`` (the run: ``progress``, ``message``, ``status``, ``ask``, ``latest``), ``@args``
    (the run's args) and its own locals.
    """

    component: str | ComponentNodeInput
    context: str | ComponentContext = ComponentContext.HTML
    local_state: Mapping[str, JSONSerializable] | None = None
    persist: Mapping[str, str | None] | Sequence[str] | None = None
    catalog: str | None = None
    description: str | None = None


def through_keys(value: str) -> list[str]:
    """The dependency keys a ``through`` prop names: ``"['robot', 'frame']"`` (as BSX writes a
    list), ``'["robot"]'`` or ``"robot, frame"`` -- the server's ``through_keys``."""
    inner = value.strip()
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1]
    return [item.strip().strip("'\"").strip() for item in inner.split(",") if item.strip()]


def _nodes(node: ComponentNodeInput) -> Iterator[ComponentNodeInput]:
    yield node
    for child in node.children or ():
        yield from _nodes(child)


def action_blok_components_in(node: ComponentNodeInput) -> list[ComponentNodeInput]:
    """The nodes of ``node`` that read a run (:data:`ACTION_BLOK_COMPONENTS`)."""
    return [each for each in _nodes(node) if each.component in ACTION_BLOK_COMPONENTS]


class _Reads:
    """Every path root an action's blok reads is one it has, and every agent it calls is a dependency."""

    def __init__(self, roots: frozenset[str], callable_keys: frozenset[str]) -> None:
        self.roots = roots
        self.callable_keys = callable_keys

    def visit_component(self, node: ComponentNodeInput, scope: dict[str, object], context: str) -> None:
        return None

    def visit_path(self, path: str, scope: dict[str, object], context: str) -> None:
        root = next((segment for segment in re.split(r"[/.]", path) if segment), "")
        if root in scope or root in self.roots:
            return
        raise ValueError(
            f"{context} reads {root!r}, which is neither a dependency of the action, `self`, `task`, "
            f"`args` nor a local of the blok (its dependencies: {sorted(self.callable_keys - {ACTION_BLOK_SELF_KEY})})"
        )

    def visit_agent_call(self, call: AgentProbeInput, scope: dict[str, object], context: str) -> None:
        if call.dependency not in self.callable_keys:
            raise ValueError(f"{context} calls {call.dependency!r}, which is not a dependency of the action")

    def visit_util_call(self, call: UtilCallInput, scope: dict[str, object], context: str) -> None:
        return None

    def declare_foreach_local(self, name: str, items_path: str, scope: dict[str, object], context: str) -> None:
        self.visit_path(items_path, scope, context)

    def declare_bound_call(self, name: str, call: AgentProbeInput, context: str) -> None:
        return None


def check_action_blok_tree(component: ComponentNodeInput, dependency_keys: frozenset[str], local_keys: frozenset[str]) -> None:
    """The server's ``check_action_blok`` for a tree: its reads, its calls, and every ``<At
    dependency>`` / ``<Route through>`` naming dependencies of the action."""
    declared = frozenset(prop.declares_value for node in _nodes(component) for prop in node.props or () if prop.declares_value)
    roots = dependency_keys | local_keys | declared | frozenset(bound_calls(component)) | _ACTION_BLOK_READS
    walk_component(component, _Reads(roots, dependency_keys | {ACTION_BLOK_SELF_KEY}))
    for node in _nodes(component):
        for prop in node.props or ():
            if not isinstance(prop.static_value, str):
                continue
            if node.component == AT_COMPONENT and prop.key == "dependency":
                named = [prop.static_value]
            elif node.component == ROUTE_COMPONENT and prop.key == "through":
                named = through_keys(prop.static_value)
            else:
                continue
            unknown = [key for key in named if key not in dependency_keys]
            if unknown:
                raise ValueError(
                    f"Blok manifest: component {node.id!r} names {unknown[0]!r}, which is not a dependency of the "
                    f"action (its dependencies: {', '.join(repr(key) for key in sorted(dependency_keys)) or 'none'})"
                )


def build_action_bloks(
    bloks: Sequence[ActionBlok],
    dependency_keys: frozenset[str],
    registry: "AppRegistry",
    owner: str,
    mode: BlokCheckMode = "warning",
) -> tuple[ActionBlokInput, ...]:
    """The wire bloks of an action, checked against its dependency keys and their catalogs.

    Raises:
        ValueError: If two bloks share a context, a context is not html, scene or overlay, a
            local is a root the renderer fills or a dependency, a persisted key is not a
            local, an overlay's root is not ``Overlay``, a path or call reaches beyond the
            action, or ``At``/``Path`` name a key that is not a dependency of the action.
    """
    built: list[ActionBlokInput] = []
    for blok in bloks:
        source = blok.component if isinstance(blok.component, str) else None
        component = parse_bsx(blok.component) if isinstance(blok.component, str) else blok.component
        context = ComponentContext(blok.context.upper() if isinstance(blok.context, str) else blok.context)
        if context.value not in ACTION_BLOK_CONTEXTS:
            raise ValueError(f"{owner}: an action's blok is html, scene or overlay, not {context.value.lower()}")
        if any(other.context == context.value for other in built):
            raise ValueError(f"{owner}: the {context.value.lower()} blok is given twice; an action has one blok per context")
        local_state = dict(blok.local_state or {})
        clashing = sorted((ACTION_BLOK_FILLED_ROOTS | dependency_keys) & set(local_state))
        if clashing:
            raise ValueError(f"{owner}: its blok cannot keep local state under {clashing}: the renderer fills it, or it names a dependency")
        persisted = (
            tuple(BlokPersistInput(key=key, description=text) for key, text in blok.persist.items())
            if isinstance(blok.persist, Mapping)
            else tuple(BlokPersistInput(key=key) for key in blok.persist or ())
        )
        unknown = sorted({local.key for local in persisted} - set(local_state))
        if unknown:
            raise ValueError(f"{owner}: its blok cannot persist {unknown}: only a local_state key is a local it can keep")
        check_context_root(component, context.value)
        check_action_blok_tree(component, dependency_keys, frozenset(local_state))
        for diagnostic in validate_blok_catalog(component, registry.catalog_view(blok.catalog), mode):
            warnings.warn(f"{owner} ({context.value.lower()} blok): {diagnostic.message}", CatalogWarning, stacklevel=4)
        # Checked as its tree; sent as the BSX it was written in, when it was.
        built.append(
            ActionBlokInput(
                context=context,
                components=None if source is not None else (component,),
                source=source,
                catalog=blok.catalog,
                description=blok.description,
                demo_state=local_state or None,
                persist=persisted,
            )
        )
    return tuple(built)


__all__ = ["ActionBlok", "build_action_bloks", "check_action_blok_tree", "through_keys"]
