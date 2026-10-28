"""A service says what its client's operations need at the server: its generated code gathers it
(turms' ``ScopesPlugin``), and every app using the service asks for it."""

from arkitekt_spec.declare.app import AppRegistry


class Client:
    pass


def test_a_service_carries_the_scopes_it_is_declared_with() -> None:
    registry = AppRegistry()

    @registry.service(scopes=["mikro_read", "mikro_write"])
    def mikro() -> Client:
        """Talks to mikro."""
        return Client()

    assert mikro.get_scopes() == ["mikro_read", "mikro_write"]


def test_a_service_declared_without_scopes_needs_none() -> None:
    registry = AppRegistry()

    @registry.service()
    def plain() -> Client:
        """Needs nothing."""
        return Client()

    assert plain.get_scopes() == []
