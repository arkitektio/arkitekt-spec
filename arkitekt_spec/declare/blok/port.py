"""A port's own blok: the widget of one argument of an action, written as BSX.

A port blok (``<Blok kind="port" context="panel">``) is drawn in the action's form where the
port's control would be. It is a field of that form: it reads the port (``port.value``,
``port.error``, ...), the form's other arguments (``args.<key>``, read only) and the state of
the agents a run would get (``self``, ``deps.<key>``), may declare queries and locals, and
writes the port's value and nothing else (``bind={port.value}``, ``port.set(...)``). It calls
no agent.

It travels as its source, on the port's widget (:class:`~arkitekt_spec.actions.BlokAssignWidgetInput`).
Here it is checked where it is mounted: against its own port, the action's arguments and the
action's dependencies.
"""

from collections.abc import Iterator, Sequence
from dataclasses import replace as dataclass_replace
from typing import TYPE_CHECKING

from arkitekt_spec.actions import (
    AgentDependencyInput,
    ArgPortInput,
    BlokAssignWidgetInput,
    StateChoiceAssignWidgetInput,
)
from arkitekt_spec.blok.bsx import parse_bsx, source_catalog
from arkitekt_spec.blok.vocabulary import BlokError
from arkitekt_spec.declare.blok.origin import (
    DeclaredAt,
    declared_at,
    finding_key,
    warn_at,
)
from arkitekt_spec.declare.blok.registry import (
    check_catalog_target,
    checked_view,
    self_dependency,
    self_reads,
)
from arkitekt_spec.declare.blok.validate import BlokScope, check_blok_tree
from arkitekt_spec.declare.catalogs import BlokCheckMode
from arkitekt_spec.declare.definition.match import build_port_match

if TYPE_CHECKING:
    from arkitekt_spec.declare.app import AppRegistry

PORT_KIND = "port"
"""The kind a port's blok is."""


def port_bloks(
    ports: Sequence[ArgPortInput],
) -> Iterator[tuple[ArgPortInput, BlokAssignWidgetInput]]:
    """Every port blok among ``ports`` and the ports below them, with the port it is the widget
    of: a port's own widget, or the one another widget falls back to."""
    for port in ports:
        widget = port.widget
        while widget is not None:
            if isinstance(widget, BlokAssignWidgetInput):
                yield port, widget
            widget = getattr(widget, "fallback", None)
        yield from port_bloks(port.children or ())


def port_scope(
    port: ArgPortInput,
    arg_keys: Sequence[str],
    dependencies: Sequence[AgentDependencyInput],
    mount: str,
) -> BlokScope:
    """Where a port's blok is mounted: its own port as ``port``, the action's arguments as
    ``args`` and the action's dependencies as ``deps`` (``self`` is the agent that runs it)."""
    return BlokScope(
        kinds=(PORT_KIND,),
        mount=mount,
        dependencies=tuple(dependencies),
        args=tuple(arg_keys),
        port=build_port_match(0, port).model_copy(update={"at": None}),
    )


def check_port_bloks(
    ports: Sequence[ArgPortInput],
    dependencies: Sequence[AgentDependencyInput],
    registry: "AppRegistry",
    owner: str,
    mode: BlokCheckMode = "warning",
    interface: str | None = None,
    at: DeclaredAt | None = None,
) -> None:
    """Check every port blok of an action where it is mounted.

    Raises:
        BlokError: If one does not parse, is no ``<Blok kind="port">``, or breaks a rule of the
            language: it calls an agent, reads an argument the action does not take, writes
            anything but ``port.value``.
    """
    arg_keys = tuple(port.key for port in ports)
    at = at or declared_at()
    for port, widget in port_bloks(ports):
        label = f"{owner} (widget of port {port.key!r}"
        try:
            catalog = source_catalog(widget.source)
            view = registry.blok_view(catalog)
            tree = parse_bsx(widget.source, view=view)
        except BlokError as error:
            raise error.within(f"{label})") from None
        scope = port_scope(
            port, arg_keys, dependencies, f"the widget of port {port.key!r}"
        )
        reported: set[str] = set()
        for finding in check_catalog_target(catalog, registry):
            if mode == "strict":
                raise BlokError.from_finding(finding)
            warn_at(
                f"{label}): {finding.message} [{finding.code}]",
                at,
                finding=finding,
                blok=interface,
            )
        for finding in check_blok_tree(tree, scope, checked_view(view)):
            if finding.level == "error" or mode == "strict":
                where = f", {finding.path}" if finding.path else ""
                raise BlokError.from_finding(
                    dataclass_replace(
                        finding,
                        path=None,
                        message=f"{label}{where}): {finding.message}",
                    ),
                    node=finding.path,
                )
            if finding_key(finding) not in reported:
                reported.add(finding_key(finding))
                warn_at(
                    f"{label}): {finding.message} [{finding.code}]",
                    at,
                    finding=finding,
                    blok=interface,
                )


def port_bloks_read_self(
    ports: Sequence[ArgPortInput], registry: "AppRegistry"
) -> bool:
    """Whether a port blok of an action reads the agent that runs it (``self``).

    Such an action is stateful: what its form shows depends on which agent it is run on, so a
    caller names that agent, and the form is drawn for it.
    """
    for _, widget in port_bloks(ports):
        tree = parse_bsx(
            widget.source, view=registry.blok_view(source_catalog(widget.source))
        )
        if self_reads(tree).agent:
            return True
    return False


def widgets_read_self(ports: Sequence[ArgPortInput], registry: "AppRegistry") -> bool:
    """Whether a widget of an action's form shows the agent that runs it: a port blok that reads
    ``self``, or a choice over the agent's own state (a state choice that names no dependency).

    Such an action is stateful: its form depends on which agent it is run on, so a caller names
    that agent, and the form is drawn for it.
    """
    if port_bloks_read_self(ports, registry):
        return True

    def own_state_choice(ports: Sequence[ArgPortInput]) -> bool:
        for port in ports:
            widget = port.widget
            while widget is not None:
                if (
                    isinstance(widget, StateChoiceAssignWidgetInput)
                    and widget.dependency is None
                ):
                    return True
                widget = getattr(widget, "fallback", None)
            if own_state_choice(port.children or ()):
                return True
        return False

    return own_state_choice(ports)


def check_port_blok_reads(registry: "AppRegistry") -> None:
    """What the port bloks of an app's actions read of ``self`` is declared by the app: the
    states they show, the actions whose latest task they watch.

    Run once the whole app is declared (a port may be declared before the state its blok reads).

    Raises:
        BlokError: If one reads a state, or watches an action, the app does not declare.
    """
    for interface in registry.implementations:
        check_action_port_blok_reads(registry, interface)


def check_action_port_blok_reads(registry: "AppRegistry", interface: str) -> None:
    """:func:`check_port_blok_reads` for one action."""
    implementation = registry.implementations[interface]
    for port, widget in port_bloks(implementation.definition.args or ()):
        tree = parse_bsx(
            widget.source, view=registry.blok_view(source_catalog(widget.source))
        )
        try:
            self_dependency(self_reads(tree), registry)
        except BlokError as error:
            raise error.within(
                f"Action {interface!r} (widget of port {port.key!r})"
            ) from None


__all__ = [
    "PORT_KIND",
    "check_action_port_blok_reads",
    "check_port_blok_reads",
    "check_port_bloks",
    "port_bloks",
    "port_bloks_read_self",
    "port_scope",
    "widgets_read_self",
]
