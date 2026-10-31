from typing import Protocol

from arkitekt_spec.actions import (
    OptimisticInput,
)


class ToOptimisticProtocol(Protocol):
    """A type that can be coerced into an OptimisticInput."""

    def to_optimistic_input(self) -> OptimisticInput: ...


OptimisticCoercible = OptimisticInput | ToOptimisticProtocol
