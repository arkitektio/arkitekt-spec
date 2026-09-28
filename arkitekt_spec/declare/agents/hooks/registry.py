"""Hooks for the agent"""

from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    Protocol,
    runtime_checkable,
)

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from arkitekt_spec.declare.errors import RegistryFrozenError

if TYPE_CHECKING:
    from arkitekt_spec.declare.state.publish import StateHolder


@runtime_checkable
class BackgroundTask(Protocol):
    """Background task that runs in the background
    This task is used to run a function in the background
    It is run in the order they are registered.
    """

    async def arun(
        self,
        agent: "StateHolder",
        contexts: dict[str, Any],
        states: dict[str, Any],
        app_context: Any = None,
    ) -> None:
        """Run the background task in the event loop
        Args:
            agent (Agent): The agent running the background task
            contexts (Dict[str, Any]): The contexts of the agent
            states (Dict[str, Any]): The state variables of the agent
            app_context (Any): The app context the agent was started with, if any
        Returns:
            None
        """
        ...


@dataclass
class StartupHookReturns:
    """Startup hook returns
    This is the return type of the startup hook.
    It contains the state variables and contexts that are used by the agent.
    """

    states: dict[str, Any]
    contexts: dict[str, Any]


@runtime_checkable
class StartupHook(Protocol):
    """Startup hook that runs when the agent starts up.
    This hook is used to setup the state variables and contexts that are used by the agent.
    It is run in the order they are registered.
    """

    async def arun(self, app_context: Any) -> StartupHookReturns:
        """Should return a dictionary of state variables"""
        ...


@runtime_checkable
class ShutdownHook(Protocol):
    """Shutdown hook that runs when the agent tears down.
    This hook is used to release whatever the app acquired during its lifetime.
    It is run in the reverse of the order they are registered.
    """

    async def arun(
        self,
        agent: "StateHolder",
        contexts: dict[str, Any],
        states: dict[str, Any],
        app_context: Any,
    ) -> None:
        """Run the shutdown hook in the event loop"""
        ...


class HooksRegistry(BaseModel):
    """Hook Registry

    Hooks are functions that are run as the agent starts up.
    They can setup the state variables and contexts that are used by the agent.
    They are run in the order they are registered.

    """

    # Declarations (``arkitekt_spec.declare.agents.hooks.declare``): the function and
    # its analysed signature. A runtime wraps each in a runner when it starts.
    background_worker: dict[str, Any] = Field(default_factory=dict)
    startup_hooks: dict[str, Any] = Field(default_factory=dict)
    shutdown_hooks: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    _frozen: bool = PrivateAttr(default=False)

    def freeze(self) -> None:
        """Refuse further registration. Done when the app that owns this is entered."""
        self._frozen = True

    def _refuse_if_frozen(self, what: str) -> None:
        if self._frozen:
            raise RegistryFrozenError(
                f"Cannot register {what} on an app that is already entered. Register "
                "before `app.run()` / `async with app:` -- what an app offers is fixed "
                "when it connects."
            )

    def register_background(self, name: str, task: Any) -> None:
        """Register a background task in the registry."""
        self._refuse_if_frozen("a background worker")
        self.background_worker[name] = task

    def register_startup(self, name: str, hook: Any) -> None:
        """Register a startup hook in the registry."""
        self._refuse_if_frozen("a startup hook")
        self.startup_hooks[name] = hook

    def register_shutdown(self, name: str, hook: Any) -> None:
        """Register a shutdown hook in the registry."""
        self._refuse_if_frozen("a shutdown hook")
        self.shutdown_hooks[name] = hook

    def __setattr__(self, name: str, value: object) -> None:
        """Refuse replacing a registry field wholesale once frozen.

        The `register_*` guards only cover writes *into* the dicts; without this,
        `registry.startup_hooks = {}` would walk straight past the freeze. Private
        names pass through, or `freeze()` could not set `_frozen` itself.
        """
        if not name.startswith("_"):
            self._refuse_if_frozen(f"'{name}'")
        super().__setattr__(name, value)

    def reset(self) -> None:
        """Reset the registry.

        Refused once frozen, like every other write here.
        """
        self._refuse_if_frozen("anything (resetting)")
        self.background_worker = {}
        self.startup_hooks = {}
        self.shutdown_hooks = {}

