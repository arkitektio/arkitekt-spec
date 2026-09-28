"""A protocol is declared on an app, and its demands' ports are built against that app.

Nothing is written on the class: the app's structure registry keeps the declared
protocol under it, so one class can be declared on any number of apps.
"""

from typing import Protocol

import pytest

from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.definition.errors import DefinitionError
from arkitekt_spec.declare.errors import RegistryFrozenError
from arkitekt_spec.declare.structures.errors import StructureDefinitionError


class Sample:
    def __init__(self, id: str) -> None:
        self.id = id


async def expand_sample(id: str) -> Sample:
    return Sample(id)


class Lab(Protocol):
    """A lab another app runs."""

    def measure(self, sample: Sample) -> float:
        """Measure a sample."""
        ...


def app_knowing_sample_as(identifier: str) -> AppRegistry:
    registry = AppRegistry()
    registry.structure(identifier, cls=Sample)(expand_sample)
    return registry


def demanded_identifier(registry: AppRegistry) -> str:
    dependency = registry.structure_registry.protocol_for(Lab).to_dependency_input(
        "lab"
    )
    (action,) = dependency.action_dependencies
    assert action.demand is not None and action.demand.arg_matches is not None
    (match,) = action.demand.arg_matches
    assert match.identifier is not None
    return match.identifier


def test_one_class_declared_on_two_apps_builds_each_apps_own_ports() -> None:
    a = app_knowing_sample_as("@a/sample")
    b = app_knowing_sample_as("@b/sample")
    a.declare(app="lab")(Lab)
    b.declare(app="lab")(Lab)

    assert (demanded_identifier(a), demanded_identifier(b)) == (
        "@a/sample",
        "@b/sample",
    )
    assert not any(name.startswith("__rekuest") for name in vars(Lab))


def test_a_parameter_annotated_with_a_declared_protocol_is_a_dependency_not_a_port() -> (
    None
):
    registry = app_knowing_sample_as("@a/sample")
    registry.declare(app="lab")(Lab)

    @registry.register
    def use(lab: Lab, n: int) -> int:
        """Use the lab."""
        return n

    implementation = registry.implementations["use"]
    assert [dependency.key for dependency in implementation.dependencies] == ["lab"]
    assert [port.key for port in implementation.definition.args] == ["n"]


def test_a_protocol_the_app_did_not_declare_is_an_unknown_port() -> None:
    registry = AppRegistry()

    with pytest.raises(DefinitionError):

        @registry.register
        def use(lab: Lab) -> None:
            """Use the lab."""


def test_declaring_needs_the_structures_the_demands_name() -> None:
    registry = AppRegistry()

    with pytest.raises(DefinitionError):
        registry.declare(app="lab")(Lab)


def test_the_same_demands_may_be_declared_again_different_ones_not() -> None:
    registry = app_knowing_sample_as("@a/sample")
    registry.declare(app="lab")(Lab)
    registry.declare(app="lab")(Lab)

    with pytest.raises(StructureDefinitionError, match="different demands"):
        registry.declare(app="other-lab")(Lab)


def test_a_snapshot_carries_the_protocols_in_maps_of_its_own() -> None:
    registry = app_knowing_sample_as("@a/sample")
    registry.declare(app="lab")(Lab)

    snapshot = registry.snapshot()

    declared = registry.structure_registry.protocol_for(Lab)
    assert snapshot.structure_registry.protocol_for(Lab) is declared
    assert (
        snapshot.structure_registry.protocols
        is not registry.structure_registry.protocols
    )
    with pytest.raises(RegistryFrozenError):
        snapshot.declare(app="lab")(Lab)


def test_a_merged_package_brings_its_protocols() -> None:
    package = app_knowing_sample_as("@a/sample")
    package.declare(app="lab")(Lab)
    registry = AppRegistry()

    registry.merge(package)

    assert registry.structure_registry.is_protocol(Lab)
    assert registry.structure_registry.protocol_for(
        Lab
    ) is package.structure_registry.protocol_for(Lab)


def test_a_blok_names_declared_protocols_by_key() -> None:
    registry = app_knowing_sample_as("@a/sample")
    registry.declare(app="lab")(Lab)

    registry.register_blok("b", "<Action key='lab' />", dependencies={"lab": Lab})

    (dependency,) = registry.registered_bloks["b"].dependencies
    assert (dependency.key, dependency.app) == ("lab", "lab")
    with pytest.raises(KeyError, match="not a protocol this app declared"):
        registry.register_blok("c", "<Action key='x' />", dependencies={"x": Sample})


# --------------------------------------------------------------------------- #
# A protocol's public annotated attributes are its state demands
# --------------------------------------------------------------------------- #


class CameraState:
    """The shape of a state the remote agent exposes."""

    connected: bool


def test_a_public_annotated_attribute_is_a_state_demand_named_after_its_class() -> None:
    from typing import Annotated, ClassVar

    from arkitekt_spec.declare.declare import demand_state

    class Camera(Protocol):
        """A camera."""

        state: CameraState
        redirected: Annotated[CameraState, demand_state(app="imagej", key="cam")]
        _private: CameraState
        version: ClassVar[int] = 1

        def snap(self) -> float:
            """Snap."""
            ...

    registry = AppRegistry()
    registry.declare(app="lab")(Camera)
    dependency = registry.structure_registry.protocol_for(Camera).to_dependency_input("camera")

    by_key = {d.key: d for d in dependency.state_dependencies or ()}
    assert set(by_key) == {"state", "redirected"}
    assert by_key["state"].demand is not None
    assert by_key["state"].demand.key == "state"
    assert by_key["redirected"].demand is not None
    assert (by_key["redirected"].demand.app, by_key["redirected"].demand.key) == ("imagej", "cam")
    assert [a.key for a in dependency.action_dependencies or ()] == ["snap"]
    assert not any(name.startswith("__rekuest") for name in vars(CameraState))


def test_an_attribute_that_is_not_a_state_shape_is_refused() -> None:
    class Broken(Protocol):
        """Broken."""

        name: str

    with pytest.raises(DefinitionError, match="not a state shape"):
        AppRegistry().declare(app="lab")(Broken)
