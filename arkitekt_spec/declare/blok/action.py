"""An action's own bloks: how a client shows a run of it, declared inline with the action.

``@app.workflow(bloks=["<Overlay>...</Overlay>", "<Html>...</Html>"])``: one blok per context,
named by its root (``<Html>`` for a task panel, ``<Scene>`` for a 3D view of its own,
``<Overlay>`` drawn into the space that hosts the run), sent with the action's definition, never
listed on its own, and not part of the action's hash. A bare tree is a blok with nothing else
declared; :class:`ActionBlok` adds locals, queries, a demo, ...; one blok needs no list. It reads the run as ``task``, its args as ``args``, the agent running
it as ``self`` and the agents the run got as ``deps.<key>`` -- the action's dependency keys, the
parameters annotated with a protocol. Checked here, at decoration, as the server checks it at
registration.
"""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from typing import TYPE_CHECKING

from arkitekt_spec.actions import (
    ACTION_BLOK_CONTEXTS,
    ActionBlokInput,
    AgentDependencyInput,
    BlokDemoInput,
    ComponentContext,
    ComponentNodeInput,
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
from arkitekt_spec.declare.blok.origin import declared_at, finding_key, warn_at
from arkitekt_spec.declare.blok.registry import catalogs_of, check_unknown_module
from arkitekt_spec.declare.blok.infer import resolve_scope
from arkitekt_spec.declare.blok.validate import BlokScope, blok_context, check_blok_tree
from arkitekt_spec.declare.catalogs import BlokCheckMode
from arkitekt_spec.scalars import JSONSerializable

if TYPE_CHECKING:
    from arkitekt_spec.declare.app import AppRegistry

ANCHOR_COMPONENT = "Anchor"
"""Anchors its children at the agent bound to ``dep``, where it stands in the hosting space."""
ROUTE_COMPONENT = "Route"
"""With ``through``, a line through the agents bound to those dependencies."""
ASK_COMPONENT = "ask"
"""The run's open ask form."""
ACTION_BLOK_COMPONENTS = (ANCHOR_COMPONENT, ROUTE_COMPONENT, ASK_COMPONENT)
"""The components that read a run or a dependency of one: an agent's blok never holds them."""


@dataclass(frozen=True)
class ActionBlok:
    """An action's own blok for one context, declared with the action (``bloks=...``).

    Args:
        component: The tree, as v2 BSX (sent as such) or already parsed. Its root names the
            context: ``<Html>`` for a task panel, ``<Scene>`` for a 3D view of its own, or
            ``<Overlay>`` drawn into the space that hosts the run (whose
            ``<Anchor dep="robot">`` anchors children at the agent the run got for ``robot``).
        locals: Its form state, written only through ``bind``: each a :class:`Local` (a port,
            its kind from its default, ``Local[T]()`` or ``Local(type=T)``), or a bare default.
        demo: What a preview renders it with (``{"task": ..., "args": ..., "deps": ...}``).
        queries: Its declared reads.
        subscriptions: Its declared streams.
        catalogs: The catalogs it needs (``"ui@1"``); those its components come from when left out.
        module: The module (an app's own extras) it renders against; the Orkestrator's when left out.
        description: What it shows of a run.
    """

    component: str | ComponentNodeInput
    locals: "Mapping[str, Local[object] | JSONSerializable] | None" = None
    demo: "BlokDemoInput | Mapping[str, JSONSerializable] | None" = None
    queries: "Mapping[str, Query] | None" = None
    subscriptions: "Mapping[str, Subscription] | None" = None
    catalogs: Sequence[str] | None = None
    module: str | None = None
    description: str | None = None


ActionBloks = ActionBlok | str | ComponentNodeInput | Sequence[ActionBlok | str | ComponentNodeInput]
"""What ``bloks=`` takes: one blok or several, each an :class:`ActionBlok` or a bare tree."""


def as_action_bloks(bloks: ActionBloks | None) -> tuple[ActionBlok, ...]:
    """``bloks=`` as a tuple of :class:`ActionBlok`: a single one (a BSX string is one) listed,
    bare trees wrapped."""
    if bloks is None:
        return ()
    listed = (bloks,) if isinstance(bloks, (ActionBlok, str, ComponentNodeInput)) else tuple(bloks)
    return tuple(each if isinstance(each, ActionBlok) else ActionBlok(each) for each in listed)


def _nodes(node: ComponentNodeInput) -> Iterator[ComponentNodeInput]:
    yield node
    for child in node.children or ():
        yield from _nodes(child)


def action_blok_components_in(node: ComponentNodeInput) -> list[ComponentNodeInput]:
    """The nodes of ``node`` that read a run (:data:`ACTION_BLOK_COMPONENTS`)."""
    return [each for each in _nodes(node) if each.component in ACTION_BLOK_COMPONENTS]


def anchored_keys(component: ComponentNodeInput) -> list[tuple[str, str]]:
    """Every dependency key an ``<Anchor dep>`` or ``<Route through>`` names, with the node's id."""
    named: list[tuple[str, str]] = []
    for node in _nodes(component):
        for prop in node.props or ():
            if node.component == ANCHOR_COMPONENT and prop.key == "dep" and isinstance(prop.static_value, str):
                named.append((prop.static_value, node.id))
            elif node.component == ROUTE_COMPONENT and prop.key == "through" and isinstance(prop.static_value, list):
                named.extend((key, node.id) for key in prop.static_value if isinstance(key, str))
    return named


def action_blok_id(interface: str, context: ComponentContext | str) -> str:
    """What names one of an action's bloks: its interface and context, ``crop:overlay``."""
    return f"{interface}:{ComponentContext(context).value.lower()}"


def build_action_bloks(
    bloks: ActionBloks,
    dependencies: Sequence[AgentDependencyInput],
    registry: "AppRegistry",
    owner: str,
    mode: BlokCheckMode = "warning",
    arg_keys: Sequence[str] | None = None,
    interface: str | None = None,
) -> tuple[ActionBlokInput, ...]:
    """The wire bloks of an action, checked against its dependencies, its args and their catalogs.

    A warning names the blok :func:`action_blok_id` (``interface`` and the blok's context), so a
    reader can tell an action's html blok from its overlay.

    Raises:
        ValueError: If two bloks share a context, a root is not ``<Html>``, ``<Scene>`` or
            ``<Overlay>``, a blok breaks a rule of the language (a :class:`BlokError` with its code), or an
            ``Anchor``/``Route`` names a key that is not a dependency of the action.
    """
    built: list[ActionBlokInput] = []
    keys = {dependency.key for dependency in dependencies}
    at = declared_at()
    for blok in as_action_bloks(bloks):
        view = registry.blok_view(blok.module)
        source = blok.component if isinstance(blok.component, str) else None
        try:
            component = parse_bsx(blok.component, view=view) if isinstance(blok.component, str) else blok.component
            context = blok_context(component)
        except BlokError as error:
            raise error.within(f"{owner} (blok)") from None
        if context.value not in ACTION_BLOK_CONTEXTS:
            raise ValueError(f"{owner}: an action's blok is an <Html>, <Scene> or <Overlay>, not <{component.component}>")
        if any(other.context == context.value for other in built):
            raise ValueError(f"{owner}: the {context.value.lower()} blok is given twice; an action has one blok per context")
        for key, node_id in anchored_keys(component):
            if key not in keys:
                raise ValueError(
                    f"{owner}: component {node_id!r} names {key!r}, which is not a dependency of the action "
                    f"(its dependencies: {', '.join(repr(k) for k in sorted(keys)) or 'none'})"
                )
        try:
            locals_ = local_inputs(blok.locals, registry)
        except BlokError as error:
            raise error.within(f"{owner} ({context.value.lower()} blok)") from None
        queries = query_inputs(blok.queries)
        subscriptions = subscription_inputs(blok.subscriptions)
        demo = demo_input(blok.demo)
        catalogs = tuple(blok.catalogs or ()) or catalogs_of(component, view)
        scope = BlokScope(
            kind="action",
            context=context.value,
            dependencies=tuple(dependencies),
            locals=locals_,
            queries=queries,
            subscriptions=subscriptions,
            args=tuple(arg_keys) if arg_keys is not None else None,
            catalogs=catalogs,
            demo=demo,
        )
        scope = resolve_scope(component, scope, view)
        reported: set[str] = set()
        blok_id = action_blok_id(interface, context) if interface is not None else None
        for finding in check_unknown_module(blok.module, registry):
            if mode == "strict":
                raise BlokError.from_finding(finding)
            warn_at(f"{owner} ({context.value.lower()} blok): {finding.message} [{finding.code}]", at, finding=finding, blok=blok_id)
        for finding in check_blok_tree(component, scope, view):
            if finding.level == "error" or mode == "strict":
                where = f", {finding.path}" if finding.path else ""
                message = f"{owner} ({context.value.lower()} blok{where}): {finding.message}"
                raise BlokError.from_finding(dataclass_replace(finding, path=None, message=message), node=finding.path)
            if finding_key(finding) not in reported:
                reported.add(finding_key(finding))
                warn_at(f"{owner} ({context.value.lower()} blok): {finding.message} [{finding.code}]", at, finding=finding, blok=blok_id)
        fields: dict[str, object] = {"context": context, "vocab": load_vocabulary().vocab}
        optional: dict[str, object] = {
            "components": None if source is not None else (component,),
            "source": source,
            "module": blok.module,
            "description": blok.description,
            "catalogs": catalogs or None,
            "locals": locals_ or None,
            "demo": demo,
            "queries": queries or None,
            "subscriptions": subscriptions or None,
        }
        fields.update({name: value for name, value in optional.items() if value is not None})
        built.append(ActionBlokInput.model_validate(fields))
    return tuple(built)


__all__ = [
    "ACTION_BLOK_COMPONENTS",
    "ActionBlok",
    "ActionBloks",
    "action_blok_components_in",
    "action_blok_id",
    "anchored_keys",
    "as_action_bloks",
    "build_action_bloks",
]
