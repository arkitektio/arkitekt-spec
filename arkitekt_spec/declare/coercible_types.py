from typing import Any, Protocol

from arkitekt_spec.actions import (
    AgentDependencyInput,
    OptimisticInput,
)


class ToDependencyProtocol(Protocol):
    """A type that can be coerced into a DependencyInput."""

    def to_dependency_input(
        self, key: str, structure_registry: Any
    ) -> AgentDependencyInput: ...


DependencyCoercible = AgentDependencyInput | ToDependencyProtocol


class ToOptimisticProtocol(Protocol):
    """A type that can be coerced into an OptimisticInput."""

    def to_optimistic_input(self) -> OptimisticInput: ...


OptimisticCoercible = OptimisticInput | ToOptimisticProtocol
