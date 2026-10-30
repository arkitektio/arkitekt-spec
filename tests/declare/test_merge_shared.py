"""What reaches a registry twice through a merge is one declaration, not a clash."""

from collections.abc import AsyncIterator

import pytest

from arkitekt_spec.declare.app import AppRegistry


def test_a_registry_two_others_took_counts_once() -> None:
    shared = AppRegistry()

    @shared.state(name="camera", persist=1.0)
    class Camera:
        exposure: int = 1

    @shared.lifecycle
    async def connect() -> AsyncIterator[Camera]:
        yield Camera()

    @shared.register
    def snap(x: int) -> int:
        """Takes a picture."""
        return x

    left, right = AppRegistry().merge(shared), AppRegistry().merge(shared)
    app = AppRegistry().merge(left).merge(right)

    assert list(app.implementations) == ["snap"]
    assert app.structure_registry.state_for(Camera).persist == 1.0, "persist travels"
    assert list(app.hooks_registry.startup_hooks) == ["connect"], "a lifecycle hook too"

    other = AppRegistry()

    @other.register(interface="snap")
    def another(x: int) -> int:
        """Not the same action."""
        return x

    with pytest.raises(ValueError, match="snap"):
        app.merge(other)
