"""Shared signature introspection for startup, shutdown and background hooks.

Every hook kind inspects the wrapped function for app-context, state and context
parameters the same way; they differ only in which of those the runtime can
actually inject at call time (see :attr:`WithVariables.injects_states`) and in
what they may return (see :meth:`WithVariables.validate_returns`).
"""

import inspect
from typing import TYPE_CHECKING, Any

from arkitekt_spec.declare.agents.context import prepare_context_variables
from arkitekt_spec.declare.agents.errors import StateRequirementsNotMet
from arkitekt_spec.declare.agents.types import BoundApp, resolve_service_clients
from arkitekt_spec.declare.protocol.types import AnyFunction
from arkitekt_spec.declare.state.utils import (
    prepare_appcontext,
    prepare_injected_variables,
    prepare_state_variables,
)

if TYPE_CHECKING:
    from arkitekt_spec.declare.structures.registry import StructureRegistry



class WithVariables:
    """Base for wrapped hooks: resolves the function's injectable parameters.

    Subclasses set :attr:`hook_kind` (used in error messages) and, where the
    runtime cannot provide states/contexts at call time, ``injects_states``.
    """

    hook_kind: str = "Hook"
    #: Whether the runtime injects state and context variables when running the
    #: hook. Startup hooks run before any state exists, so only the app context
    #: can be injected there.
    injects_states: bool = True
    #: Tells the agent this hook accepts ``bound_app=`` in ``arun``. Hook classes
    #: written against the bare protocols do not, and are called as before.
    takes_bound_app: bool = True

    def __init__(
        self, func: AnyFunction, structure_registry: "StructureRegistry | None" = None
    ) -> None:
        self.func = func
        self.state_variables, self.state_returns = prepare_state_variables(func, structure_registry)
        self.app_context_variables, self.app_context_returns = prepare_appcontext(func, structure_registry)
        self.context_variables, self.context_returns = prepare_context_variables(func, structure_registry)
        # The registry says which annotations are a service's client; a hook
        # registered without one can take no client.
        self.injected_variables = prepare_injected_variables(func, structure_registry)
        self.pass_app_context = self.app_context_variables.count > 0

        self._validate_arguments(func)
        self.validate_returns(func)

    # ------------------------------------------------------------------ checks

    def _validate_arguments(self, func: AnyFunction) -> None:
        parameters = inspect.signature(func).parameters
        # The app's clients exist before any state does, so every hook kind can
        # take them. A hook runs for no task, so it cannot take a Task.
        injectable = list(self.app_context_variables.app_context_variables.keys())
        injectable += list(self.injected_variables.service_client_variables)
        if self.injected_variables.task_variables:
            raise ValueError(
                f"{self.hook_kind} function {func.__name__} asks for a Task, but a "
                "hook runs for no task. Report through a service client instead."
            )
        if self.injects_states:
            injectable += list(self.state_variables.variable_keys) + list(
                self.context_variables.context_variables.keys()
            )

        if len(parameters) > len(injectable):
            incorrect_args = set(parameters.keys()) - set(injectable)
            what = (
                "app-context, state and context variables"
                if self.injects_states
                else "app-context variables (states and contexts do not exist yet when it runs)"
            )
            raise ValueError(
                f"{self.hook_kind} function {func.__name__} has more arguments than the {what}. "
                f"Expected at most {len(injectable)} arguments, but got {len(parameters)}. "
                f"{incorrect_args} are not valid argument names."
            )

    def validate_returns(self, func: AnyFunction) -> None:
        """Hook for subclasses to constrain what the function may return."""

    # ----------------------------------------------------------------- kwargs

    def get_kwargs(
        self,
        contexts: dict[str, Any],
        states: dict[str, Any],
        app_context: Any = None,
        bound_app: BoundApp | None = None,
    ) -> dict[str, Any]:
        """Build the call kwargs from the agent's live contexts, states, app context and app.

        ``app_context`` is the object the caller passed to ``run(context=...)``;
        ``bound_app`` is the app the agent belongs to, which supplies the clients
        the hook asks for by annotation. A hook that asks for the app context gets
        exactly the declared kind: the agent refuses to start otherwise, and this
        is the last line of defence for a hook driven by hand.
        """
        kwargs: dict[str, Any] = {}
        for key, value in self.context_variables.context_variables.items():
            try:
                kwargs[key] = contexts[value]
            except KeyError as e:
                raise StateRequirementsNotMet(
                    f"Context requirements not met: {e}"
                ) from e

        for mapping in (
            self.state_variables.read_only_variables,
            self.state_variables.write_state_variables,
        ):
            for key, value in mapping.items():
                try:
                    kwargs[key] = states[value]
                except KeyError as e:
                    raise StateRequirementsNotMet(
                        f"State requirements not met: {e}. Available are {list(states.keys())}"
                    ) from e

        for key, cls in self.app_context_variables.app_context_variables.items():
            if not isinstance(app_context, cls):
                raise StateRequirementsNotMet(
                    "App context requirements not met: the agent was not started "
                    f"with a {cls.__name__} app context"
                )
            kwargs[key] = app_context

        # No task is passed: a hook runs for no assignment, so its calls are
        # attributed to whatever is ambient, which at startup is nothing.
        kwargs.update(
            resolve_service_clients(
                self.injected_variables.service_client_variables,
                bound_app,
                whose=f"{self.hook_kind} function {self.func.__name__}",
            )
        )

        return kwargs
