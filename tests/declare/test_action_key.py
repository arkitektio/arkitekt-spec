"""An action's key: what the server identifies it by, settable instead of the function name.

Actions built by one factory share a ``__name__``. Without an explicit key they
all get the same definition key, and the server refuses the registration as
one agent implementing the same action twice.
"""

from collections.abc import Callable

from arkitekt_spec.declare.app import AppRegistry


def make_setter(line: str) -> Callable[[float], float]:
    def set_power(power_mw: float) -> float:
        """Set the laser power."""
        return power_mw

    return set_power


def test_register_action_keys_the_definition_and_defaults_the_interface() -> None:
    registry = AppRegistry()

    for line in ("488", "561"):
        registry.register_action(make_setter(line), key=f"set_laser_{line}")

    assert {
        interface: implementation.definition.key
        for interface, implementation in registry.implementations.items()
    } == {"set_laser_488": "set_laser_488", "set_laser_561": "set_laser_561"}


def test_an_explicit_interface_still_wins_over_the_key() -> None:
    registry = AppRegistry()

    registry.register_action(make_setter("488"), key="set_laser_488", interface="laser")

    assert registry.implementations["laser"].definition.key == "set_laser_488"


def test_without_a_key_the_function_name_is_used() -> None:
    registry = AppRegistry()

    registry.register_action(make_setter("488"))

    assert registry.implementations["set_power"].definition.key == "set_power"


def test_register_forwards_the_key_too() -> None:
    registry = AppRegistry()

    registry.register(key="set_laser_640")(make_setter("640"))

    assert registry.implementations["set_laser_640"].definition.key == "set_laser_640"


def test_register_action_returns_the_function_still_callable() -> None:
    registry = AppRegistry()

    wrapped = registry.register_action(make_setter("488"), key="set_laser_488")

    assert wrapped(3.0) == 3.0
    assert wrapped.interface == "set_laser_488"
