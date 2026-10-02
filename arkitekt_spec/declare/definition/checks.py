"""The checks of an agent declaration that need more than a single model.

The models carry every rule that needs nothing but themselves
(:mod:`arkitekt_spec.rules`). What is left here needs the declaration around them:
a search query against its widget's filters and dependencies (its full shape too,
when graphql-core is installed), a state reference against dependencies and the
agent's own states, and blok components against their dependencies.

They run once, on the assembled declaration (:meth:`AppRegistry.to_implement_agent_input`),
walking down to every port, widget and blok -- the one place that sees all of it.
"""

from collections.abc import Iterable, Iterator

from arkitekt_spec.actions import (
    AgentDependencyInput,
    ArgPortInput,
    BlokImplementationInput,
    ImplementAgentInput,
    ImplementationInput,
    SearchAssignWidgetInput,
    StateChoiceAssignWidgetInput,
    StateImplementationInput,
)


def _ports(ports: Iterable[ArgPortInput]) -> Iterator[ArgPortInput]:
    """Every port, depth first through its children."""
    for port in ports:
        yield port
        yield from _ports(port.children or ())


def check_search_widget(widget: SearchAssignWidgetInput, owner: str) -> None:
    """The query parses into the shape a search runs, and every variable is backed."""
    from arkitekt_spec.scalars import (
        RESERVED_SEARCH_VARIABLES,
        get_search_query_variables,
        validate_search_query,
    )

    query = validate_search_query(widget.query)
    filter_keys = {f.key for f in (widget.filters or ())}
    dependency_keys = set(widget.dependencies or ())
    available = filter_keys | dependency_keys
    for variable in get_search_query_variables(query):
        if variable in RESERVED_SEARCH_VARIABLES:
            continue
        if variable not in available:
            raise ValueError(
                f"{owner}: search query variable '${variable}' is not backed by a filter port"
                f" or a dependency. Available filters: {sorted(filter_keys)},"
                f" dependencies: {sorted(dependency_keys)}"
            )


def _check_state_choice(
    port: ArgPortInput,
    widget: StateChoiceAssignWidgetInput,
    dependencies: tuple[AgentDependencyInput, ...],
    own_states: tuple[StateImplementationInput, ...],
    owner: str,
) -> None:
    """A STATE_CHOICE path resolves: through its dependency, or against the agent's own states."""
    from arkitekt_spec.declare.blok.validate import resolve_state_reference

    if widget.state_path is None:
        return
    resolve_state_reference(
        widget.dependency,
        widget.state_path,
        dependencies=dependencies,
        own_states=own_states if widget.dependency is None else (),
        context=f"state choice widget for port '{port.key}' in {owner}",
    )


def check_implementation(
    implementation: ImplementationInput,
    own_states: tuple[StateImplementationInput, ...] | None = None,
) -> ImplementationInput:
    """Search queries and state-choice paths of one implementation.

    Run where the implementation is registered, so the error names the decorated
    function. A ``self.`` state reference needs the agent's own states, so it is
    only resolved when ``own_states`` is given (by :func:`check_agent_input`).
    """
    name = implementation.interface or implementation.definition.name
    owner = f"implementation '{name}'"
    dependencies = tuple(implementation.dependencies or ())
    for port in _ports(implementation.definition.args or ()):
        widget = port.widget
        if isinstance(widget, SearchAssignWidgetInput):
            check_search_widget(widget, f"Port '{port.key}' of {owner}")
        elif isinstance(widget, StateChoiceAssignWidgetInput):
            if widget.dependency is None and own_states is None:
                continue
            _check_state_choice(port, widget, dependencies, own_states or (), owner)
    return implementation


def check_blok(blok: BlokImplementationInput) -> BlokImplementationInput:
    """Every component of a blok is valid against the blok's dependencies."""
    from arkitekt_spec.declare.blok.validate import local_roots_of, validate_blok

    dependencies = list(blok.dependencies or ())
    local_roots = local_roots_of(dependencies, blok.demo_state)
    for component in blok.components or ():
        validate_blok(component, dependencies, local_roots=local_roots)
    return blok


def check_agent_input(agent: ImplementAgentInput) -> ImplementAgentInput:
    """Run the rekuest-side checks over a whole declaration, and hand it back."""
    own_states = tuple(agent.states or ())
    for implementation in agent.implementations or ():
        check_implementation(implementation, own_states)
    for blok in agent.bloks or ():
        check_blok(blok)
    return agent
