"""A service may say which image hosts the server its client talks to.

It is the one thing a client package knows about its server that a deployment
made for a test needs: the rest, the image says for itself. It describes the
package, not the app, so it stays out of the manifest.
"""

from arkitekt_spec.declare.app import AppRegistry


class Client:
    pass


def test_a_service_says_which_image_hosts_it() -> None:
    registry = AppRegistry()

    @registry.service(image="example/thing:3")
    def thing() -> Client:
        """A thing."""
        return Client()

    assert thing.image == "example/thing:3"
    assert registry.services["thing"].image == "example/thing:3"


def test_a_service_need_not_say() -> None:
    registry = AppRegistry()

    @registry.service()
    def thing() -> Client:
        """A thing."""
        return Client()

    assert thing.image is None


def test_the_image_travels_with_the_service_into_an_app() -> None:
    package, app = AppRegistry(), AppRegistry()

    @package.service(image="example/thing:3")
    def thing() -> Client:
        """A thing."""
        return Client()

    app.register_service(thing)

    assert app.services["thing"].image == "example/thing:3"
