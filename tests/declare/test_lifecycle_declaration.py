"""What a lifecycle hook's annotation says it yields."""

from collections.abc import AsyncIterator

from arkitekt_spec.declare.app import AppRegistry


def test_a_lifecycle_hook_may_yield_nothing() -> None:
    registry = AppRegistry()

    @registry.lifecycle
    async def nothing() -> AsyncIterator[None]:
        yield

    @registry.lifecycle
    async def bare() -> AsyncIterator:  # type: ignore[type-arg]
        yield

    assert list(registry.hooks_registry.startup_hooks) == ["nothing", "bare"]
