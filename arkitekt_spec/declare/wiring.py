"""How a service says what it needs from the platform.

A service's builder is an ordinary function whose parameters are typed with the
markers below -- ``Annotated[Alias, Require("live.arkitekt.mikro")]`` for a service
instance it needs, ``TokenLoader`` to authenticate, ``Annotated[Alias, Own()]`` for
the server the app itself logged into. Reading those is declaration; resolving them
(the fakts client) happens when a runtime builds the service.
"""

import ipaddress
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from arkitekt_spec.manifest import Requirement

#: The tailnet's address range: a host in it is only reachable over the mesh.
_TAILNET = ipaddress.IPv4Network("100.64.0.0/10")

#: The class attribute a client of the platform's configuration service sets, so
#: that a service parameter typed with it receives the whole client.
FAKTS_MARKER = "__arkitekt_fakts__"


def is_fakts_client(annotation: Any) -> bool:
    """Whether a parameter annotation asks for the whole configuration client."""
    return isinstance(annotation, type) and getattr(annotation, FAKTS_MARKER, False) is True


class Alias(BaseModel):
    """An alias is a way of contacting a service instance in Fakts.

    It contains the host, port, ssl flag, path and challenge.
    """

    id: str
    """The unique identifier of the alias."""
    host: str
    port: int | None = None
    """The port is optional, if not set, the default port for the service will be"""
    ssl: bool = False
    """The ssl flag indicates if the service should be accessed via SSL or not. If set to True, the service will be accessed via HTTPS, otherwise it will be accessed via HTTP."""
    path: str | None = None
    """The path is optional, if not set, the default path for the service will be used."""
    challenge: str = Field(
        default="",
        description="""The challenge is a string that is used to verify the alias. It should be """,
    )
    public: bool = False
    """Whether this alias is reachable from outside the deployment's own
    network. Informational: the server decides which aliases to hand out,
    the client just tries them in order."""
    kind: str | None = None
    """How the server reaches the instance: ``"absolute"``, ``"relative"`` or
    ``"mesh"`` (only reachable over the deployment's tailnet). Older servers
    do not send it; see :meth:`is_mesh`."""
    proxy: str | None = Field(default=None, exclude=True)
    """The HTTP proxy this alias is reached through (the mesh node's local
    proxy), set by the fakts client when it resolves the alias. Never sent or
    cached."""

    def is_mesh(self) -> bool:
        """Whether this alias is only reachable over the mesh.

        The server says so with ``kind``; for servers that do not send it, a
        host in the tailnet range 100.64.0.0/10 is taken to mean the same.
        """
        if self.kind is not None:
            return self.kind == "mesh"
        try:
            return ipaddress.IPv4Address(self.host) in _TAILNET
        except ValueError:
            return False

    @property
    def challenge_path(self) -> str:
        """The challenge_path of the alias. Its a reachable http path that can be used to verify if the alias is accessible by the client."""
        return self.to_http_path(self.challenge)

    def to_http_path(self, append: str | None = None) -> str:
        """Convert the alias to a HTTP path

        This method converts the alias to a HTTP path, which can be used to access the service.
        If the port is not set, the default port for the service will be used.
        If the ssl flag is set, the service will be accessed via HTTPS, otherwise it will be accessed via HTTP.

        Args:
            append (Optional[str], optional): An optional string to append to the path. Defaults to None.

        Returns:
            str: The HTTP path for the service
        """
        protocol = "https" if self.ssl else "http"

        url = f"{protocol}://{self.host}"
        if self.port:
            url += f":{self.port}"
        if self.path:
            url += f"/{self.path.lstrip('/')}"
        if append:
            url += f"/{append.lstrip('/')}"

        return url

    def to_ws_path(self, append: str | None = None) -> str:
        """Convert the alias to a WebSocket path

        This method converts the alias to a WebSocket path, which can be used to access the service.
        If the port is not set, the default port for the service will be used.
        If the ssl flag is set, the service will be accessed via wss, otherwise it will be accessed via ws.

        Args:
            append (Optional[str], optional): An optional string to append to the path. Defaults to None.

        Returns:
            str: The WebSocket path for the service
        """
        protocol = "wss" if self.ssl else "ws"

        url = f"{protocol}://{self.host}"
        if self.port:
            url += f":{self.port}"
        if self.path:
            url += f"/{self.path.lstrip('/')}"
        if append:
            url += f"/{append.lstrip('/')}"

        return url


@dataclass(frozen=True)
class Own:
    """Marks a parameter as the app's *own* fakts server, not a required service.

    Written as ``Annotated[Alias, Own()]``. A service built on this declares no
    requirement -- there is nothing for a deployment to provision, because the
    address is the one the app already authenticated against. unlok is the only
    service built this way.
    """


@dataclass(frozen=True)
class Require:
    """Marks a parameter as one of a service's requirements.

    Written as ``Annotated[Fakt, Require("live.arkitekt.mikro")]``. The parameter
    *name* becomes the requirement's key, so the thing a builder passes to a link
    is the same thing that put the requirement in the manifest -- they cannot
    drift.

    Args:
        service: The service that fills the key, in reverse domain naming.
        description: What it is, shown to the user when access is asked for.
        optional: Whether the client still works without it.
    """

    service: str
    description: str | None = None
    optional: bool = False

    def to_requirement(self, key: str) -> Requirement:
        """Build the requirement this marks, keyed by the parameter's name.

        Args:
            key: The parameter name, which is the fakts key.

        Returns:
            The requirement, as it goes into the manifest.
        """
        return Requirement(
            key=key,
            service=self.service,
            optional=self.optional,
            description=self.description,
        )


@runtime_checkable
class TokenLoader(Protocol):
    """A way to get, and renew, an access token.

    The two operations :class:`~fakts.contrib.rath.auth.FaktsAuthLink` needs --
    which is all any service needs of fakts once its addresses are resolved.
    :class:`~fakts.Fakts` satisfies this structurally, so nothing has to adapt it.
    """

    async def aget_token(self) -> str:
        """Get a valid access token, fetching or renewing one if needed.

        Returns:
            The token, without a ``Bearer`` prefix.
        """
        ...

    async def arefresh_token(self, stale_token: str | None = None) -> str:
        """Renew the access token after one was rejected.

        Args:
            stale_token: The token that was just refused, when it is known.
                Concurrent and retried 401s that pass the same stale token
                collapse into a single renewal -- without it, each retry rotates
                the refresh token again, spending credentials to re-solve a
                problem the first renewal already fixed.

        Returns:
            The renewed token.
        """
        ...
