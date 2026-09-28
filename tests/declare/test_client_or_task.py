"""A parameter is a service's client, the running task, or a port; asking must never raise.

``Task`` is a runtime-checkable protocol with data members (``id``), which cannot be an
``issubclass`` target: asking whether it is a client used to raise, so no action taking
``task: Task`` could register in an app that declares a service.
"""

from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.task import Task


class ThingClient:
    """What the ``things`` service builds."""


def registry_with_client() -> AppRegistry:
    registry = AppRegistry()

    @registry.service()
    def things() -> ThingClient:
        return ThingClient()

    return registry


def test_the_task_protocol_is_not_a_client() -> None:
    registry = registry_with_client()

    assert not registry.structure_registry.is_client(Task)
    assert not registry.structure_registry.is_client(Task | None)
    assert registry.structure_registry.is_client(ThingClient)


def test_an_action_takes_a_client_and_the_task_beside_its_ports() -> None:
    registry = registry_with_client()

    @registry.register
    def count(x: int, things: ThingClient, task: Task) -> int:
        """Count."""
        return x

    definition = registry.implementations["count"].definition
    assert [arg.key for arg in definition.args] == ["x"]
