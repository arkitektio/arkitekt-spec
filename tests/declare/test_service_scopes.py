"""A service's client says what its operations need at the server: its generated part carries
``__scopes__`` (turms' ``client_fields``), and every app using the service asks for them."""

from typing import ClassVar

import pytest

from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.service import ServiceDefinitionError


class GeneratedApi:
    """What turms generates: the operations, and what they need."""

    __scopes__: ClassVar[tuple[str, ...]] = ("mikro_write", "mikro_read")


class Client(GeneratedApi):
    pass


class Plain:
    pass


def test_a_service_says_what_its_client_class_needs() -> None:
    registry = AppRegistry()

    @registry.service()
    def mikro() -> Client:
        """Talks to mikro."""
        return Client()

    assert mikro.get_scopes() == ["mikro_read", "mikro_write"]


def test_a_client_that_says_nothing_needs_nothing() -> None:
    registry = AppRegistry()

    @registry.service()
    def plain() -> Plain:
        """Needs nothing."""
        return Plain()

    assert plain.get_scopes() == []


def test_scopes_that_are_no_names_are_refused() -> None:
    class Broken:
        __scopes__ = "mikro_read"

    registry = AppRegistry()

    @registry.service()
    def broken() -> Broken:
        """Says it wrong."""
        return Broken()

    with pytest.raises(ServiceDefinitionError, match="not a collection of scope names"):
        broken.get_scopes()
