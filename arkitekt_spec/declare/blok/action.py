"""An action's own bloks: how a client shows a run of it, declared with the action.

``@app.workflow(bloks=['<Blok kind="action" context="scene"><Space room=...>...</Space></Blok>', ...])``:
each blok is a ``<Blok>`` whose kind says when it is shown -- ``action`` for a run of it,
``assign`` for starting one -- and whose context says where (``panel`` for a task panel,
``scene`` for a 3D view holding one ``<Scene>``, or ``<Space>`` for one that places the run's
dependencies in a room whose layout is shared); one blok per kind and context. They are sent
with the action's definition, never listed on their own, and not part of the action's hash. A
bare tree is a blok with nothing else said; :class:`ActionBlok` adds a demo and a description.
An action blok reads the run as ``task``, its args as ``args``, the agent running it as
``self`` and the agents the run got as ``deps.<key>`` -- the action's dependency keys, the
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
from arkitekt_spec.blok.bsx import parse_bsx, source_catalog
from arkitekt_spec.blok.declared import blok_catalog, blok_header
from arkitekt_spec.blok.vocabulary import BlokError, load_vocabulary
from arkitekt_spec.declare.blok.declarations import demo_input
from arkitekt_spec.declare.blok.origin import declared_at, finding_key, warn_at
from arkitekt_spec.declare.blok.registry import check_catalog_target, checked_view, minor_needed
from arkitekt_spec.declare.blok.validate import BlokScope, check_blok_tree
from arkitekt_spec.declare.catalogs import BlokCheckMode
from arkitekt_spec.scalars import JSONSerializable

if TYPE_CHECKING:
    from arkitekt_spec.declare.app import AppRegistry

SPACE_COMPONENT = "Space"
"""A 3D view placing the run's dependencies in a room whose layout is shared."""
PLACED_COMPONENT = "Placed"
"""Draws the agent bound to ``dep`` with its own scene blok, where the room keeps it."""
ROUTE_COMPONENT = "Route"
"""With ``through``, a line through the agents bound to those dependencies."""
ASK_COMPONENT = "ask"
"""The run's open ask form."""
ACTION_BLOK_COMPONENTS = (SPACE_COMPONENT, PLACED_COMPONENT, ROUTE_COMPONENT, ASK_COMPONENT)
"""The components that read a run or a dependency of one: an agent's blok never holds them."""


@dataclass(frozen=True)
class ActionBlok:
    """An action's own blok for one kind and context, declared with the action (``bloks=...``).

    Args:
        component: The tree, as v2 BSX (sent as such) or already parsed: a
            ``<Blok kind="action|assign" context="panel|scene">``. A scene holds one
            ``<Scene>``, or one ``<Space room="room:lab">`` that places the run's dependencies
            in a room whose layout is shared (``<Placed dep="robot">`` draws the agent the run
            got for ``robot`` with its own scene blok).
        demo: What a preview renders it with (``{"task": ..., "args": ..., "deps": ...}``).
        description: What it shows of a run.
    """

    component: str | ComponentNodeInput
    demo: "BlokDemoInput | Mapping[str, JSONSerializable] | None" = None
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


def action_blok_id(interface: str, context: ComponentContext | str, kind: str = "action") -> str:
    """What names one of an action's bloks: its interface and context (``crop:scene``), an assign
    blok's with its phase (``crop:assign:panel``)."""
    phase = "assign:" if kind == "assign" else ""
    return f"{interface}:{phase}{ComponentContext(context).value.lower()}"


ACTION_KINDS = ("action", "assign")
"""The kinds an action's own blok is: one for a run of it, one for starting one."""


def build_action_bloks(
    bloks: ActionBloks,
    dependencies: Sequence[AgentDependencyInput],
    registry: "AppRegistry",
    owner: str,
    mode: BlokCheckMode = "warning",
    arg_keys: Sequence[str] | None = None,
    interface: str | None = None,
) -> tuple[ActionBlokInput, ...]:
    """The wire bloks of an action, checked against its dependencies and its args.

    Each blok's ``<Blok kind>`` says when it is shown: ``action`` for a run (it reads ``task``),
    ``assign`` for starting one (no ``task``; ``<args />`` and ``assign.start()`` allowed). A
    warning names the blok :func:`action_blok_id` (``interface``, its kind and context), so a
    reader can tell an action's panel from its scene.

    Raises:
        ValueError: If two bloks share a kind and context, a blok is no ``<Blok kind="action">``
            or ``<Blok kind="assign">`` in a panel or a scene, or breaks a rule of the language
            (a :class:`BlokError` with its code), a ``Placed``/``Route`` naming a key that is no
            dependency of the action among them.
    """
    built: list[ActionBlokInput] = []
    at = declared_at()
    for blok in as_action_bloks(bloks):
        source = blok.component if isinstance(blok.component, str) else None
        try:
            catalog = source_catalog(blok.component) if isinstance(blok.component, str) else blok_catalog(blok.component)
            view = registry.blok_view(catalog)
            component = parse_bsx(blok.component, view=view) if isinstance(blok.component, str) else blok.component
            header = blok_header(component)
        except BlokError as error:
            raise error.within(f"{owner} (blok)") from None
        context = ComponentContext(header.context)
        kind = header.kind
        phase = "assign " if kind == "assign" else ""
        label = f"{owner} ({phase}{context.value.lower()} blok"
        if kind in ACTION_KINDS and context.value not in ACTION_BLOK_CONTEXTS:
            raise ValueError(f"{owner}: an action's blok is a panel or a scene, not a {context.value.lower()} (an action's logo goes in logo=)")
        if kind in ACTION_KINDS and any(other.context == context.value and other.phase == ("ASSIGN" if kind == "assign" else "RUN") for other in built):
            raise ValueError(f"{owner}: the {phase}{context.value.lower()} blok is given twice; an action has one {phase}blok per context")
        if kind == "assign" and any(node.component == ASK_COMPONENT for node in _nodes(component)):
            raise ValueError(f"{owner}: an assign blok is shown before there is a task, so it holds no 'ask'")
        demo = demo_input(blok.demo)
        scope = BlokScope(
            kinds=ACTION_KINDS,
            mount=f"bloks= of {interface or owner}",
            dependencies=tuple(dependencies),
            args=tuple(arg_keys) if arg_keys is not None else None,
            demo=demo,
        )
        reported: set[str] = set()
        blok_id = action_blok_id(interface, context, kind) if interface is not None else None
        for finding in check_catalog_target(catalog, registry):
            if mode == "strict":
                raise BlokError.from_finding(finding)
            warn_at(f"{label}): {finding.message} [{finding.code}]", at, finding=finding, blok=blok_id)
        for finding in check_blok_tree(component, scope, checked_view(view)):
            if finding.level == "error" or mode == "strict":
                where = f", {finding.path}" if finding.path else ""
                message = f"{label}{where}): {finding.message}"
                raise BlokError.from_finding(dataclass_replace(finding, path=None, message=message), node=finding.path)
            if finding_key(finding) not in reported:
                reported.add(finding_key(finding))
                warn_at(f"{label}): {finding.message} [{finding.code}]", at, finding=finding, blok=blok_id)
        fields: dict[str, object] = {"context": context, "vocab": load_vocabulary().vocab}
        if kind == "assign":
            # Set only for an assign blok: a run's blok keeps the bytes it had before there were phases.
            fields["phase"] = "ASSIGN"
        optional: dict[str, object] = {
            "components": None if source is not None else (component,),
            "source": source,
            "catalog": catalog,
            "catalog_minor": minor_needed(component, view) or None,
            "description": blok.description,
            "demo": demo,
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
    "as_action_bloks",
    "build_action_bloks",
]
