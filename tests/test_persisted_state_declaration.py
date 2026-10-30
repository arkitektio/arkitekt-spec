"""A startup hook takes a state's old value only if the state is persisted."""

import pytest

from arkitekt_spec.declare.app import AppRegistry


def test_a_startup_hook_takes_a_persisted_states_old_value() -> None:
    registry = AppRegistry()

    @registry.state(name="camera", persist=1.0)
    class Camera:
        exposure: int = 1

    @registry.startup
    def start(old: Camera | None) -> Camera:
        return old or Camera()

    hook = registry.hooks_registry.startup_hooks["start"]
    assert hook.previous_state_variables == {"old": "camera"}
    assert hook.get_startup_kwargs(None, None, {"camera": Camera(exposure=3)})["old"].exposure == 3
    assert hook.get_startup_kwargs(None, None)["old"] is None, "a first run has none"


def test_a_state_that_is_not_durable_cannot_be_taken() -> None:
    registry = AppRegistry()

    @registry.state(name="camera")
    class Camera:
        exposure: int = 1

    with pytest.raises(ValueError, match="persist="):

        @registry.startup
        def unpersisted(old: Camera | None) -> Camera:
            return Camera()

    with pytest.raises(ValueError, match=r"SomeState \| None"):

        @registry.startup
        def bare(old: Camera) -> Camera:
            return Camera()
