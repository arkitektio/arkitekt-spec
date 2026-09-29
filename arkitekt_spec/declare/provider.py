"""A provider: how an app's offerings are served, declared beside its services.

A service builds a *client*; a provider builds the *agent* that provides what the
app offers. It is declared on the registry the service package ships, beside the
service, and injected the same way -- plus the clients a run has already built,
by annotation::

    @registry.service(...)
    def rekuest(rekuest: Annotated[Alias, Require("live.arkitekt.rekuest")],
                s3: Annotated[Alias, Require("live.arkitekt.s3")],
                tokens: TokenLoader, registry: AppRegistry) -> Rekuest: ...

    @registry.provider()
    def rekuest_agent(rekuest: Annotated[Alias, Require("live.arkitekt.rekuest")],
                      tokens: TokenLoader, manifest: AppManifest,
                      registry: AppRegistry) -> RekuestAgent: ...

A run builds every client first, then the provider, then owns the agent: it binds
the run to it, applies the run's options and drives it (``aprovide``). The
provider function neither binds nor starts anything. An app is served by one
agent, so a registry holds at most one provider.
"""

import inspect
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast

from arkitekt_spec.declare.service import (
    _ALIAS,
    _CLIENT,
    _MANIFEST,
    _OWN,
    _REGISTRY,
    _Injection,
    _read_injections,
)
from arkitekt_spec.manifest import Requirement

if TYPE_CHECKING:

    from arkitekt_spec.declare.app import AppRegistry

A = TypeVar("A")
"""What a provider's function returns: the agent."""


class ProviderDefinitionError(TypeError):
    """A provider's signature does not say what a run needs to know."""


class ProviderBuildError(LookupError):
    """A run could not build the provider's agent."""


class Provider(Generic[A]):
    """A declared provider: what it requires, what it returns, and how to build it.

    Made by :meth:`~rekuest.app.AppRegistry.provider`; a package never constructs
    one directly. An app takes it in (``App(providers=[...])`` or implicitly, on
    its first offering), reads its requirements into the manifest, and a run calls
    :meth:`build` once every client exists.

    Attributes:
        name: The function's name.
        returns: What its function returns: the agent's class.
        registry: The registry it was declared on.
    """

    def __init__(
        self,
        function: Callable[..., Any],
        name: str,
        returns: type[A],
        injections: Sequence[_Injection],
        registry: "AppRegistry",
    ) -> None:
        self._function = function
        self._injections = list(injections)
        self.name = name
        self.returns: type[A] = returns
        self.registry = registry

    def __repr__(self) -> str:
        keys = ", ".join(r.key for r in self.get_requirements()) or "nothing"
        return f"Provider({self.name!r}, requires {keys})"

    def get_requirements(self) -> list[Requirement]:
        """What this provider needs from a deployment, read off the signature."""
        return [
            injection.require.to_requirement(injection.name)
            for injection in self._injections
            if injection.kind == _ALIAS and injection.require is not None
        ]

    @property
    def needs_fakts(self) -> bool:
        """Whether building this provider needs a run's fakts.

        Only a provider that takes the registry and built clients alone does not.
        """
        return any(
            injection.kind not in (_REGISTRY, _CLIENT) for injection in self._injections
        )

    async def build(
        self,
        fakts: Any,
        registry: "AppRegistry",
        clients: Mapping[str, Any],
    ) -> A:
        """Build the agent, once, from resolved addresses and the run's clients.

        Args:
            fakts: The run's fakts, or ``None`` for a run that built none.
            registry: The run's registry snapshot: what the agent serves.
            clients: The run's built clients, by service name.

        Returns:
            The agent, not bound and not started: the run does both.

        Raises:
            ProviderBuildError: If the provider needs fakts and the run has none,
                asks for a client no service of the run returned, or returns
                something that is not an agent.
        """
        kwargs: dict[str, Any] = {}
        for injection in self._injections:
            if injection.kind == _CLIENT:
                cls = injection.cls
                assert cls is not None, "a client injection carries its class"
                client = next((c for c in clients.values() if isinstance(c, cls)), None)
                if client is None:
                    raise ProviderBuildError(
                        f"'{self.name}' asks for a {cls.__name__} as "
                        f"'{injection.name}', but this run built none. Add the "
                        "service that returns it to the app."
                    )
                kwargs[injection.name] = client
            elif injection.kind == _REGISTRY:
                kwargs[injection.name] = registry
            else:
                if fakts is None:
                    raise ProviderBuildError(
                        f"'{self.name}' needs the run's fakts for '{injection.name}', "
                        "but this run built none: the app has no requirements."
                    )
                if injection.kind == _ALIAS:
                    require = injection.require
                    assert require is not None, "an alias injection carries its Require"
                    kwargs[injection.name] = (
                        await fakts.aget_alias_or_none(injection.name)
                        if require.optional
                        else await fakts.aget_alias(injection.name)
                    )
                elif injection.kind == _OWN:
                    kwargs[injection.name] = await fakts.aget_self_alias()
                elif injection.kind == _MANIFEST:
                    kwargs[injection.name] = fakts.manifest
                else:
                    kwargs[injection.name] = fakts

        built = self._function(**kwargs)
        agent = await built if inspect.isawaitable(built) else built
        if not all(hasattr(agent, name) for name in _AGENT_LIFECYCLE):
            raise ProviderBuildError(
                f"'{self.name}' returned {type(agent).__name__}, which is not an "
                "agent: it must offer aprovide, aconnect, aloop and force."
            )
        return cast(A, agent)


#: What a runtime's agent offers, checked by shape: the agent types are the runtime's.
_AGENT_LIFECYCLE = ("aprovide", "aconnect", "aloop", "force")


def declare_provider(registry: "AppRegistry", function: Callable[..., A]) -> Provider[A]:
    """Read a provider off ``function``, declared on ``registry``.

    The function's name is the provider's, its return annotation is the agent's
    class, and its parameters are what a run hands it: the same injections as a
    service, plus any client a service declared on ``registry`` returns.

    Raises:
        ProviderDefinitionError: If the signature does not say what a run needs.
    """
    name = function.__name__
    try:
        returns, injections = _read_injections(
            name, function, clients_of=registry.structure_registry
        )
    except TypeError as e:
        raise ProviderDefinitionError(str(e)) from None
    return Provider(
        function=function,
        name=name,
        returns=returns,
        injections=injections,
        registry=registry,
    )


__all__ = ["Provider", "ProviderBuildError", "ProviderDefinitionError", "declare_provider"]
