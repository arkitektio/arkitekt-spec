"""Type-level definitions shared by the agent package.

Protocol-like markers, type variables and the decorators that stamp them. Data
containers live in :mod:`rekuest.agents.dataclasses`; behaviour lives on
:class:`rekuest.agents.base.BaseAgent`.
"""

from collections.abc import Mapping
from typing import Any, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")


@runtime_checkable
class BoundApp(Protocol):
    """What an agent is bound to: the running app, answering for its clients.

    Three ways to ask, because three things ask differently:

    * ``get(Mikro)`` -- by class, which is how an action, a hook and a
      structure's expander name the client they want.
    * ``clients`` -- by service name (``{"mikro": <Mikro>}``), which is how a
      *declared* structure finds the client that expands it: its service is
      recorded on it, not the class.
    * ``services`` -- the service names the app declares, which is how the agent
      reports a structure whose service the app lacks. Declared, not built, so a
      service whose client was never constructed still counts.

    arkitekt's Runtime is one; a test can pass anything that answers.
    """

    # Read-only: a mutable protocol attribute is invariant, so a runtime exposing
    # these as properties (or as a plain dict) could never satisfy it.
    @property
    def services(self) -> Mapping[str, Any]:
        """The service names the app declares, to what declares them."""
        ...

    @property
    def clients(self) -> Mapping[str, Any]:
        """The app's built clients, by service name."""
        ...

    def get(self, key: type[T], /) -> T | None:
        """The app's client of class ``key``, or ``None``."""
        ...


AppContext = Any
"""The app context: whatever object the caller hands to ``run(context=...)``.

Its class is declared with ``@app.app_context``; nothing is written on it.
"""


def unwrap_injectable(annotation: object) -> object:
    """Strip ``Annotated[...]`` and ``Optional[...]`` from an injectable annotation.

    ``Optional[Mikro]`` still asks for the client; the ``None`` only says the author
    guards for its absence. Any other union is returned as is and is not injectable.
    Whether the class *is* a client is the structure registry's to say
    (:meth:`~rekuest.structures.registry.StructureRegistry.is_client`): it is one
    when a registered service returns it.
    """
    import types
    import typing

    while True:
        origin = typing.get_origin(annotation)
        if origin is typing.Annotated:
            annotation = typing.get_args(annotation)[0]
            continue
        if origin is typing.Union or origin is types.UnionType:
            args = [a for a in typing.get_args(annotation) if a is not type(None)]
            if len(args) == 1:
                annotation = args[0]
                continue
        return annotation


def resolve_service_clients(
    variables: Mapping[str, type],
    bound_app: "BoundApp | None",
    *,
    whose: str,
    task: Any = None,
) -> dict[str, Any]:
    """Fill each parameter with the app's client of the class it asked for.

    The one place a client-typed parameter is turned into a client. Actions,
    hooks and a structure's expander all inject the same way, and each used to
    spell this loop out again -- two of them had already drifted apart on how the
    client was handed over.

    Args:
        variables: Parameter name to the client class it wants, from
            ``prepare_injected_variables``.
        bound_app: The app the agent belongs to, or ``None`` when it has none.
        whose: What is asking, for the error message ("action 'segment'").
        task: The assignment being run, when there is one. Kept for the error
            messages and for callers that still pass it; attribution itself is
            ambient now (:data:`rath.task.current_task`), so every task gets the
            same shared client and still attributes its own requests.

    Returns:
        The kwargs, one per parameter.

    Raises:
        StateRequirementsNotMet: If the app has no client of a wanted class.
            Passing ``None`` instead would only move the failure into the
            function body, away from its cause.
    """
    from arkitekt_spec.declare.agents.errors import StateRequirementsNotMet

    if not variables:
        return {}
    if bound_app is None:
        raise StateRequirementsNotMet(
            f"{whose} asks for a client, but this agent is not bound to an app "
            "that has any."
        )

    kwargs: dict[str, Any] = {}
    for key, cls in variables.items():
        client = bound_app.get(cls)
        if client is None:
            raise StateRequirementsNotMet(
                f"{whose} asks for a {cls.__name__} as '{key}', but the app was "
                "built without one. Add its service to the app."
            )
        # The one shared client, not a per-task copy of it. What attributes its
        # requests to this task is `rath.task.current_task`, which the actor sets
        # around the body -- so an object the call returns remembers the client
        # rather than a view that dies with the assignment.
        kwargs[key] = client
    return kwargs


__all__ = [
    "AppContext",
    "BoundApp",
    "T",
    "resolve_service_clients",
    "unwrap_injectable",
]
