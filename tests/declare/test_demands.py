"""An app's protocols compared with the app meant to serve them, offline."""

from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.demands import PORT_MISMATCH, PROVIDER_ABSENT, check_demands


def robot() -> AppRegistry:
    registry = AppRegistry()

    @registry.register
    def move(trajectory: str, speed: float = 1.0) -> bool:
        """Move along a trajectory."""
        return True

    return registry


def test_a_protocol_its_provider_serves_has_no_findings() -> None:
    lab = AppRegistry()

    class Robot:
        def move(self, trajectory: str) -> bool:
            """Move along a trajectory."""

    lab.structure_registry.declare(app="robot")(Robot)

    # The provider takes one argument more than is demanded: a demand is a prefix.
    assert check_demands(lab, {"robot": robot()}) == []
    # Nothing to compare with is a note, never an error.
    (note,) = check_demands(lab, {})
    assert (note.level, note.code) == ("note", PROVIDER_ABSENT)


def test_a_renamed_argument_is_found_at_the_method_that_demands_it() -> None:
    lab = AppRegistry()

    class Robot:
        def move(self, name: str) -> bool:
            """Move along a trajectory."""

    lab.structure_registry.declare(app="robot")(Robot)

    (finding,) = check_demands(lab, {"robot": robot()})
    assert (finding.level, finding.code, finding.slot) == (
        "error",
        PORT_MISMATCH,
        "move",
    )
    assert (
        "'name' (STRING) is expected at 0, where there is 'trajectory' (STRING)"
        in finding.message
    )
    assert (
        finding.file == __file__ and finding.line == Robot.move.__code__.co_firstlineno
    )


def test_a_protocol_method_carries_how_its_calls_are_to_be_treated() -> None:
    """``@policy`` is the caller's, said where the dependency is declared: it is kept on the
    declared method for the calls, and is no part of what is demanded of the other app."""
    import pytest

    from arkitekt_spec.declare.definition.policy import CallPolicy, policy, seconds

    assert [seconds(d) for d in (5, "5s", "500ms", "2m", "1h")] == [
        5.0,
        5.0,
        0.5,
        120.0,
        3600.0,
    ]
    with pytest.raises(ValueError, match="not a duration"):
        seconds("soon")
    with pytest.raises(ValueError, match="FAIL, RESUME, RESTART"):
        policy(on_lost="AGAIN")

    lab = AppRegistry()

    @lab.declare(app="robot")
    class Robot:
        @policy(inquire_after="5s", on_lost="FAIL")
        def move(self, x: int) -> int:
            """Moves."""

        def stop(self) -> None:
            """Stops."""

    protocol = lab.structure_registry.protocols[Robot]
    assert protocol.actions["move"].policy == CallPolicy(
        inquire_after=5.0, on_lost="FAIL"
    )
    assert protocol.actions["move"].policy.options() == {
        "inquire_after": 5.0,
        "on_lost": "FAIL",
    }
    assert protocol.actions["stop"].policy is None
    with_policy = protocol.to_dependency_input(protocol.interface).model_dump_json()
    assert "inquire" not in with_policy and "on_lost" not in with_policy
