import pytest

from arkitekt_spec.declare.app import AppRegistry

# Hooks and states belong to a registry, as they would to an app.
_REGISTRY = AppRegistry()


def test_startup_decorator() -> None:
    @_REGISTRY.startup
    def my_startup_hook() -> None:
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


def test_a_startup_hook_annotated_none_returns_nothing() -> None:
    registry = AppRegistry()

    @registry.startup
    def annotated() -> None:
        pass

    @registry.startup
    async def annotated_async() -> None:
        pass

    @registry.startup
    def annotated_as_string() -> "None":
        pass

    @registry.startup
    def unannotated():  # noqa: ANN202 -- what this test is about: a hook without a return annotation
        pass

    hooks = registry.hooks_registry.startup_hooks
    assert list(hooks) == [
        "annotated",
        "annotated_async",
        "annotated_as_string",
        "unannotated",
    ]
    for hook in hooks.values():
        assert hook.state_returns.count == 0
        assert hook.context_returns.count == 0


def test_a_startup_hook_returning_neither_state_nor_context_is_refused() -> None:
    registry = AppRegistry()

    with pytest.raises(ValueError, match="more return values"):

        @registry.startup
        def returns_an_int() -> int:
            return 1


def test_shutdown_decorator() -> None:
    @_REGISTRY.context
    class Tschau:
        pass

    @_REGISTRY.state
    class TschauState:
        pass

    @_REGISTRY.shutdown
    def my_shutdown_hook() -> None:
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
