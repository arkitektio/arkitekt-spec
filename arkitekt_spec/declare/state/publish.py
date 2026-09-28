from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from arkitekt_spec.actions import ReturnPortInput


@dataclass
class Patch:
    op: str
    path: str
    value: Any = None
    old_value: Any = None
    port: ReturnPortInput | None = None
    correlation_id: str | None = None

    def __str__(self) -> str:
        return (
            "Patch("
            f"op={self.op}, path={self.path}, value={self.value}, "
            f"old_value={self.old_value}, port={getattr(self.port, 'key', None)}"
            ")"
        )


@runtime_checkable
class StateHolder(Protocol):
    """Protocol for publisher functions"""

    def publish_patch(self, interface: str, patch: Patch) -> None:
        """Method to publish a change to a specific field of the state

        Args:
            interface: The state interface name (e.g., "StageState")
            patch: The patch containing op, path, value, and old_value
        """
        ...
