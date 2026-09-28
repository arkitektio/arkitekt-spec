from __future__ import annotations

from collections.abc import Awaitable
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AnyFunction(Protocol):
    """A function that takes a passport and a transport and returns an actor.
    This method will create the actor and return it.
    """

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Create the actor and return it. This method will create the actor and
        return it.
        """
        ...

    @property
    def __name__(self) -> str:
        """Get the name of the function. This method will return the name of the
        function.
        """
        ...


@runtime_checkable
class AnyState(Protocol):
    """An instance of a class an app registered as a state.

    Being a state is a declaration on an app, not a property of the class, so
    there is nothing structural to check here: any instance qualifies. (Kept
    runtime-checkable because pydantic validates fields typed with it.)
    """


AnyContext = Any

SingleStartUpReturn = AnyContext | AnyState | None
StartupTypes = SingleStartUpReturn | tuple[SingleStartUpReturn, ...]


@runtime_checkable
class BackgroundFunction(Protocol):
    """A function that takes a passport and a transport and returns an actor.
    This method will create the actor and return it.
    """

    def __call__(self, *args: Any, **kwargs: Any) -> Awaitable[None] | None:
        """Create the actor and return it. This method will create the actor and
        return it.
        """
        ...

    @property
    def __name__(self) -> str:
        """Get the name of the function. This method will return the name of the
        function.
        """
        ...


@runtime_checkable
class AsyncBackgroundFunction(Protocol):
    """A function that takes a passport and a transport and returns an actor.
    This method will create the actor and return it.
    """

    def __call__(self, *args: Any, **kwargs: Any) -> Awaitable[None]:
        """Create the actor and return it. This method will create the actor and
        return it.
        """
        ...

    @property
    def __name__(self) -> str:
        """Get the name of the function. This method will return the name of the
        function.
        """
        ...


@runtime_checkable
class ThreadedBackgroundFunction(Protocol):
    """A function that takes a passport and a transport and returns an actor.
    This method will create the actor and return it.
    """

    def __call__(
        self, *args: AnyState | AnyContext, **kwargs: AnyState | AnyContext
    ) -> None:
        """Create the actor and return it. This method will create the actor and
        return it.
        """
        ...

    @property
    def __name__(self) -> str:
        """Get the name of the function. This method will return the name of the
        function.
        """
        ...


@runtime_checkable
class ShutdownFunction(Protocol):
    """A function that is run when the agent tears down. It may declare state,
    context and app-context arguments, and must not return anything.
    """

    def __call__(self, *args: Any, **kwargs: Any) -> Awaitable[None] | None:
        """Release whatever the app acquired during its lifetime."""
        ...

    @property
    def __name__(self) -> str:
        """Get the name of the function. This method will return the name of the
        function.
        """
        ...


@runtime_checkable
class AsyncShutdownFunction(Protocol):
    """A shutdown function that runs in the event loop."""

    def __call__(self, *args: Any, **kwargs: Any) -> Awaitable[None]:
        """Release whatever the app acquired during its lifetime."""
        ...

    @property
    def __name__(self) -> str:
        """Get the name of the function. This method will return the name of the
        function.
        """
        ...


@runtime_checkable
class ThreadedShutdownFunction(Protocol):
    """A shutdown function that runs in a thread."""

    def __call__(
        self, *args: AnyState | AnyContext, **kwargs: AnyState | AnyContext
    ) -> None:
        """Release whatever the app acquired during its lifetime."""
        ...

    @property
    def __name__(self) -> str:
        """Get the name of the function. This method will return the name of the
        function.
        """
        ...


@runtime_checkable
class StartupFunction(Protocol):
    """A function that takes a passport and a transport and returns an actor.
    This method will create the actor and return it.
    """

    def __call__(self, app_context: Any) -> Awaitable[StartupTypes] | StartupTypes:
        """Create the actor and return it. This method will create the actor and
        return it.
        """
        ...

    @property
    def __name__(self) -> str:
        """Get the name of the function. This method will return the name of the
        function.
        """
        ...


@runtime_checkable
class ContextLessStartupFunction(Protocol):
    """A function that takes a passport and a transport and returns an actor.
    This method will create the actor and return it.
    """

    def __call__(self) -> Awaitable[StartupTypes] | StartupTypes:
        """Create the actor and return it. This method will create the actor and
        return it.
        """
        ...

    @property
    def __name__(self) -> str:
        """Get the name of the function. This method will return the name of the
        function.
        """
        ...


@runtime_checkable
class AsyncStartupFunction(Protocol):
    """A function that takes a passport and a transport and returns an actor.
    This method will create the actor and return it.
    """

    def __call__(self, app_context: Any) -> Awaitable[StartupTypes]:
        """Create the actor and return it. This method will create the actor and
        return it.
        """
        ...

    @property
    def __name__(self) -> str:
        """Get the name of the function. This method will return the name of the
        function.
        """
        ...


@runtime_checkable
class ThreadedStartupFunction(Protocol):
    """A function that takes a passport and a transport and returns an actor.
    This method will create the actor and return it.
    """

    def __call__(self, app_context: Any) -> StartupTypes:
        """Create the actor and return it. This method will create the actor and
        return it.
        """
        ...

    @property
    def __name__(self) -> str:
        """Get the name of the function. This method will return the name of the
        function.
        """
        ...
