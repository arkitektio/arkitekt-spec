"""A provider: how an app's offerings are served, by the agent it builds.

A client is what an app *uses*; a provider builds the *agent* that serves what it
*offers*. It is a function, declared like a client's binder -- plus any client a
run has already built, by annotation::

    @provider(clients=[Rekuest])
    def rekuest_agent(rekuest: Annotated[Alias, Require("live.arkitekt.rekuest")],
                      tokens: TokenLoader, manifest: AppManifest,
                      registry: AppRegistry) -> RekuestAgent: ...

A run builds every client first, then the provider, then owns the agent: it binds
the run to it and drives it. Building is the run's; this only says what it takes.
"""

from collections.abc import Callable, Sequence
from typing import Any, Generic, TypeVar, get_type_hints

from arkitekt_spec.declare.injections import (
    BinderDefinitionError,
    Injection,
    needs_fakts,
    read_injections,
    requirements_of,
)
from arkitekt_spec.manifest import Requirement

A = TypeVar("A")
"""What a provider's function returns: the agent."""


class ProviderDefinitionError(BinderDefinitionError):
    """A provider's signature does not say what a run needs to know."""


class Provider(Generic[A]):
    """A declared provider: what it requires, what it returns, and its function.

    Attributes:
        name: The function's name.
        function: What a run calls, with what :attr:`injections` ask for.
        returns: The agent's class.
        injections: What each parameter asks for.
        clients: Clients an app served by it uses whether or not it names them
            (rekuest's agent talks to the server through ``Rekuest``).
    """

    def __init__(
        self,
        function: Callable[..., Any],
        name: str,
        returns: type[A],
        injections: Sequence[Injection],
        clients: Sequence[type] = (),
    ) -> None:
        self.function = function
        self.name = name
        self.returns: type[A] = returns
        self.injections = list(injections)
        self.clients = tuple(clients)

    def __repr__(self) -> str:
        keys = ", ".join(r.key for r in self.get_requirements()) or "nothing"
        return f"Provider({self.name!r}, requires {keys})"

    def get_requirements(self) -> list[Requirement]:
        """What this provider needs from a deployment, read off the signature."""
        return requirements_of(self.injections)

    @property
    def needs_fakts(self) -> bool:
        """Whether building it needs a run's fakts."""
        return needs_fakts(self.injections)


def provider(
    *, clients: Sequence[type] = ()
) -> Callable[[Callable[..., A]], Provider[A]]:
    """Declare a provider: ``@provider()`` on the function building the agent.

    Args:
        clients: ``@client`` classes every app it serves uses.

    Raises:
        ProviderDefinitionError: If the signature does not say what a run needs.
    """

    def declare(function: Callable[..., A]) -> Provider[A]:
        name = function.__name__
        returns = get_type_hints(function).get("return")
        if not isinstance(returns, type):
            raise ProviderDefinitionError(
                f"'{name}' does not say what it returns: annotate it with the agent's class."
            )
        try:
            injections = read_injections(name, function, clients=True)
        except BinderDefinitionError as e:
            raise ProviderDefinitionError(str(e)) from None
        return Provider(function, name, returns, injections, clients)

    return declare


__all__ = ["Provider", "ProviderDefinitionError", "provider"]
