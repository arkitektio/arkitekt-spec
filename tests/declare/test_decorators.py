
import pytest

from arkitekt_spec.declare.app import AppRegistry

# Hooks and states belong to a registry, as they would to an app.
_REGISTRY = AppRegistry()


def test_startup_decorator():
    @_REGISTRY.startup
    def my_startup_hook():
        pass

    @_REGISTRY.context
    class Hallo:
        pass

    @_REGISTRY.state
    class HalloState:
        pass

    @_REGISTRY.startup
    def my_startup_hook_returns_context() -> Hallo:
        return Hallo()

    @_REGISTRY.startup
    def my_startup_hook_returns_context_and_state() -> tuple[Hallo, HalloState]:
        return Hallo(), HalloState()


def test_shutdown_decorator():
    @_REGISTRY.context
    class Tschau:
        pass

    @_REGISTRY.state
    class TschauState:
        pass

    @_REGISTRY.shutdown
    def my_shutdown_hook():
        pass

    @_REGISTRY.shutdown
    async def my_async_shutdown_hook(tschau: Tschau, tschau_state: TschauState) -> None:
        pass

    with pytest.raises(ValueError):

        @_REGISTRY.shutdown
        def my_shutdown_hook_returns_state() -> TschauState:
            return TschauState()

    with pytest.raises(ValueError):

        @_REGISTRY.shutdown
        def my_shutdown_hook_with_unknown_arg(unknown: int) -> None:
            pass
