"""Checking what one app demands of another, with both declarations in hand.

A declared protocol is a set of demands on a remote app: an action per method,
a state per attribute, each with the ports it expects. The server compares them
when a task is assigned and refuses one it cannot bind. :func:`check_demands`
is the same comparison done before anything runs, between an app's registry and
the registries of the apps meant to serve it.

The rule is the server's: a port match finds a port equal in every field the
match sets (``at``, ``key``, ``kind``, ``identifier``, ``dimension``,
``nullable``), its ``children`` the same way below it; ``force_arg_length`` and
``force_return_length`` fix the count. Three things a server knows and a
registry does not are left alone: a demand by ``hash``, a pinned ``version``,
and a match's ``descriptors``.
"""

import inspect
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from arkitekt_spec.actions import PortMatchInput

if TYPE_CHECKING:
    from arkitekt_spec.declare.app import AppRegistry

ACTION_MISSING = "dependency-action-missing"
PORT_MISMATCH = "dependency-port-mismatch"
STATE_MISSING = "dependency-state-missing"
STATE_MISMATCH = "dependency-state-mismatch"
PROVIDER_ABSENT = "dependency-provider-absent"


@dataclass(frozen=True)
class DemandFinding:
    """One thing a declared protocol demands that its provider does not offer.

    Attributes:
        level: ``error`` for a demand the provider at hand does not meet: the
            server refuses to assign a task depending on it. ``note`` for one
            that could not be compared, as its provider is not at hand.
        code: Which kind of finding, one of the ``dependency-*`` codes.
        message: What is wrong, naming the port.
        provider: The identifier of the app the protocol is directed at.
        protocol: The qualified name of the protocol class.
        slot: The method or attribute of the protocol the finding is about, if
            it is about one.
        file: The file declaring the protocol (its method, for an action).
        line: The line of that declaration.
        consumer: The identifier of the app that declared the protocol, when
            whoever ran the check knows it.
    """

    level: Literal["error", "note"]
    code: str
    message: str
    provider: str
    protocol: str
    slot: str | None = None
    file: str | None = None
    line: int | None = None
    consumer: str | None = None

    @property
    def subject(self) -> str:
        """What the finding is about: the protocol, and its method or attribute."""
        return f"{self.protocol}.{self.slot}" if self.slot else self.protocol

    def __str__(self) -> str:
        """The finding as it is printed: who demands what, what is wrong, and where it is declared."""
        who = f"{self.consumer}: " if self.consumer else ""
        where = f"\n    {self.file}:{self.line}" if self.file else ""
        return f"{who}{self.subject}: {self.message}{where}"


def _declared_at(obj: Any) -> tuple[str | None, int | None]:
    try:
        file = inspect.getsourcefile(obj)
        _, line = inspect.getsourcelines(obj)
    except (OSError, TypeError):
        return None, None
    return (os.path.abspath(file) if file else None), line


def _kind(value: Any) -> str:
    return str(getattr(value, "value", value))


def _shown(
    key: str | None, kind: Any, identifier: str | None, nullable: bool | None
) -> str:
    """A port or a match as a message names it: ``'name' (STRING, nullable)``."""
    details = [_kind(kind)] if kind is not None else []
    if identifier:
        details.append(identifier)
    if nullable:
        details.append("nullable")
    name = f"'{key}'" if key is not None else "a port"
    return f"{name} ({', '.join(details)})" if details else name


def _fits(match: PortMatchInput, port: Any) -> bool:
    """Whether ``port`` equals ``match`` in every field the match sets, children included."""
    if match.key is not None and match.key != port.key:
        return False
    if match.kind is not None and _kind(match.kind) != _kind(port.kind):
        return False
    if match.identifier is not None and match.identifier != port.identifier:
        return False
    if match.nullable is not None and match.nullable != bool(port.nullable):
        return False
    if match.dimension is not None and match.dimension != getattr(
        port, "dimension", None
    ):
        return False
    return unmet_match(match.children or (), port.children or ()) is None


def unmet_match(matches: Sequence[PortMatchInput], ports: Sequence[Any]) -> str | None:
    """Why ``ports`` do not satisfy ``matches``, or ``None`` when they do.

    Args:
        matches: The demanded ports.
        ports: The ports of the action or state that should meet them.

    Returns:
        The first match no port meets, said for a person, or ``None``.
    """
    for match in matches:
        wanted = _shown(match.key, match.kind, match.identifier, match.nullable)
        if match.at is None:
            if not any(_fits(match, port) for port in ports):
                return f"nothing like {wanted} is there"
            continue
        if match.at >= len(ports):
            return f"{wanted} is expected at {match.at}, where there is nothing"
        port = ports[match.at]
        if not _fits(match, port):
            found = _shown(port.key, port.kind, port.identifier, bool(port.nullable))
            return f"{wanted} is expected at {match.at}, where there is {found}"
    return None


def _state_of(provider: "AppRegistry", key: str) -> Any | None:
    """The provider's state a demand of ``key`` reaches: by key first, then by interface."""
    states = list(provider.structure_registry.states.values())
    for attribute in ("key", "interface"):
        for state in states:
            if getattr(state, attribute, None) == key:
                return state
    return None


def check_demands(
    consumer: "AppRegistry", providers: Mapping[str, "AppRegistry"]
) -> list[DemandFinding]:
    """Compare every protocol ``consumer`` declares with the app it is directed at.

    Args:
        consumer: The registry of the app declaring protocols.
        providers: The registries of the apps at hand, by app identifier. A
            protocol directed at an app that is not among them is noted, not
            refused: nothing says that app is wrong.

    Returns:
        The findings, in declaration order; empty when every demand that could
        be compared is met.
    """
    findings: list[DemandFinding] = []
    for cls, protocol in consumer.structure_registry.protocols.items():
        dependency = protocol.to_dependency_input(protocol.interface)
        app = dependency.app
        if not app:
            # Directed at no app: bound by whoever assigns, so there is nothing to compare.
            continue
        name = cls.__qualname__
        class_file, class_line = _declared_at(cls)
        provider = providers.get(app)
        if provider is None:
            findings.append(
                DemandFinding(
                    level="note",
                    code=PROVIDER_ABSENT,
                    message=f"{name} is directed at {app!r}, which is not among the apps checked",
                    provider=app,
                    protocol=name,
                    file=class_file,
                    line=class_line,
                )
            )
            continue

        def found(code: str, slot: str, message: str) -> None:
            method = getattr(cls, slot, None)  # noqa: B023
            file, line = _declared_at(method) if callable(method) else (None, None)
            findings.append(
                DemandFinding(
                    level="error",
                    code=code,
                    message=message,
                    provider=app,  # noqa: B023
                    protocol=name,  # noqa: B023
                    slot=slot,
                    file=file or class_file,  # noqa: B023
                    line=line or class_line,  # noqa: B023
                )
            )

        for action in dependency.action_dependencies or ():
            demand = action.demand
            if demand is None or demand.hash:
                continue
            key = demand.key or action.key
            implementation = provider.implementations.get(key)
            if implementation is None:
                found(ACTION_MISSING, action.key, f"{app} offers no action {key!r}")
                continue
            definition = implementation.definition
            args, returns = definition.args or (), definition.returns or ()
            if demand.name is not None and demand.name != definition.name:
                found(
                    PORT_MISMATCH,
                    action.key,
                    f"{app}'s {key} is named {definition.name!r}, not {demand.name!r}",
                )
                continue
            for what, matches, ports, forced in (
                ("argument", demand.arg_matches, args, demand.force_arg_length),
                ("return", demand.return_matches, returns, demand.force_return_length),
            ):
                if forced is not None and forced != len(ports):
                    found(
                        PORT_MISMATCH,
                        action.key,
                        f"{app}'s {key} has {len(ports)} {what}s, not {forced}",
                    )
                    break
                reason = unmet_match(matches or (), ports)
                if reason is not None:
                    found(
                        PORT_MISMATCH, action.key, f"{app}'s {key}, {what}s: {reason}"
                    )
                    break

        for state in dependency.state_dependencies or ():
            state_demand = state.demand
            if state_demand is None or state_demand.hash:
                continue
            key = state_demand.key or state.key
            declared = _state_of(provider, key)
            if declared is None:
                found(STATE_MISSING, state.key, f"{app} has no state {key!r}")
                continue
            reason = unmet_match(
                state_demand.matches or (), declared.definition.ports or ()
            )
            if reason is not None:
                found(STATE_MISMATCH, state.key, f"{app}'s state {key}: {reason}")
    return findings


__all__ = [
    "ACTION_MISSING",
    "PORT_MISMATCH",
    "PROVIDER_ABSENT",
    "STATE_MISMATCH",
    "STATE_MISSING",
    "DemandFinding",
    "check_demands",
    "unmet_match",
]
