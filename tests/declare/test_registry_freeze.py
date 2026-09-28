"""A frozen registry refuses every write, by whichever route.

What an agent offers is fixed at the moment it tells the server, so the registry
it was assembled in is closed then. These are the registry's own guarantees --
*when* the freeze happens is the owning app's business, and arkitekt tests that.
"""

import pytest

from arkitekt_spec.declare.agents.hooks.registry import HooksRegistry
from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.errors import RegistryFrozenError


class Frame:
    """An object that only ever lives in this process."""


def test_the_register_methods_are_refused() -> None:
    registry = AppRegistry()
    registry.freeze()

    with pytest.raises(RegistryFrozenError):

        @registry.register
        def late(x: int) -> int:
            """Too late."""
            return x

    with pytest.raises(RegistryFrozenError):
        registry.register_memory_structure(Frame)

    with pytest.raises(RegistryFrozenError):
        registry.register_blok(name="b", component="<Action key='nope' />")


def test_freezing_an_app_registry_freezes_its_hooks_too() -> None:
    registry = AppRegistry()
    registry.freeze()

    assert registry.hooks_registry._frozen


def test_replacing_a_field_wholesale_is_refused() -> None:
    """The `register_*` guards only cover writes *into* the dicts.

    Without a `__setattr__` guard, `registry.states = {}` walks straight past a
    freeze that every other route respects.
    """
    registry = AppRegistry()
    registry.freeze()

    with pytest.raises(RegistryFrozenError):
        registry.implementations = {}
    with pytest.raises(RegistryFrozenError):
        registry.states = {}


def test_the_hooks_registry_refuses_its_own_writes_and_its_reset() -> None:
    hooks = HooksRegistry()
    hooks.freeze()

    with pytest.raises(RegistryFrozenError):
        hooks.register_startup("s", lambda: None)
    with pytest.raises(RegistryFrozenError):
        hooks.reset()
    with pytest.raises(RegistryFrozenError):
        hooks.startup_hooks = {}


def test_a_registry_that_was_never_frozen_still_takes_everything() -> None:
    """The control: none of the above is refused on the way in."""
    registry = AppRegistry()

    registry.register_memory_structure(Frame)

    @registry.register
    def in_time(frame: Frame) -> None:
        """Registered before the freeze."""

    assert "in_time" in registry.implementations
    assert not registry._frozen
