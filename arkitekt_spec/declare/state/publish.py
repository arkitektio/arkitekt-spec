from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from arkitekt_spec.actions import ReturnPortInput

CLEANUP = "cleanup"
"""A patch's ``lifecycle``: a task wrote it while being stopped (cancel, interrupt), on
its way out."""
SHUTDOWN = "shutdown"
"""A patch's ``lifecycle``: written outside any task while the agent shuts down (its
shutdown hooks, and what follows a lifecycle hook's yield)."""


@dataclass
class Patch:
    op: str
    path: str
    value: Any = None
    old_value: Any = None
    port: ReturnPortInput | None = None
    correlation_id: str | None = None
    lifecycle: str | None = None
    """Where in a task's or the agent's life the change was made, when not in its ordinary
    course (``CLEANUP``, ``SHUTDOWN``). The runtime says it; an app cannot."""
    qualifiers: tuple[str, ...] = ()
    """What the app says the change was for (``task.qualified("homing")``): any names, its own."""

    def __str__(self) -> str:
        return (
            "Patch("
            f"op={self.op}, path={self.path}, value={self.value}, "
            f"old_value={self.old_value}, port={getattr(self.port, 'key', None)}"
            ")"
        )


@runtime_checkable
class StateHolder(Protocol):
    """Where a state's updates go: the agent that adopted it."""

    def publish_update(self, interface: str, patches: Sequence[Patch]) -> None:
        """Take one update of a state: the operations that make it up, in order.

        An update is the unit a state changes by. Whoever follows the state sees all of
        its operations or none of them, so they are handed over together.

        Args:
            interface: The state interface name (e.g., "StageState")
            patches: The operations of the update, each with op, path and value
        """
        ...
