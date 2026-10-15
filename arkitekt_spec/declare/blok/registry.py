"""Turn registered blok declarations into the inputs the agent uploads."""

from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from typing import (
    TYPE_CHECKING,
    Any,
)

from arkitekt_spec.actions import (
    ActionDependencyInput,
    AgentDependencyInput,
    AgentProbeInput,
    BlokImplementationInput,
    ComponentNodeInput,
    PortKind,
    ReturnPortInput,
    StateDependencyInput,
    UtilCallInput,
)
from arkitekt_spec.declare.blok.validate import (
    EVENT_SCOPE_KEY,
    RESERVED_ROOTS,
    idle_task_record,
    latest_task_reads,
)
from arkitekt_spec.declare.blok.walk import (
    BlokVisitor,
    action_key_for,
    bound_calls,
    walk_component,
)
from arkitekt_spec.declare.catalogs import LATEST_SEGMENT, asset_path_parts, latest_task_path_parts
from arkitekt_spec.declare.definition.checks import check_blok
from arkitekt_spec.declare.definition.dependencies import (
    build_action_dependency_input,
    build_state_dependency_input,
)

if TYPE_CHECKING:
    from arkitekt_spec.declare.app import AppRegistry

# The dependency key a blok's own agent goes by.
SELF_KEY = "self"


def build_declared_bloks(
    app_registry: "AppRegistry",
) -> dict[str, BlokImplementationInput]:
    """Generate blok inputs from their declarations against an app registry."""
    declared_bloks: dict[str, BlokImplementationInput] = {}

    for blok_key, declaration in app_registry.registered_bloks.items():
        dependencies = _build_dependencies_for_component(
            declaration.component,
            app_registry,
            declaration.dependencies or (),
            local_state=declaration.local_state or {},
            demo_state=declaration.demo_state or {},
        )

        demo_state = declaration.demo_state
        if demo_state is None:
            demo_state = _autogenerate_demo_state(dependencies, app_registry)
        if declaration.local_state:
            # The wire has one field for both: the server takes every top-level
            # demo_state key as a path root, and the renderer seeds its data
            # model from it.
            demo_state = {**demo_state, **declaration.local_state}
        demo_state = demo_state_for(declaration.component, dependencies, demo_state)

        fields: dict[str, Any] = dict(
            key=blok_key,
            dependencies=tuple(dependencies),
            description=declaration.description,
            demo_state=demo_state,
            catalog=declaration.catalog,
            context=declaration.context,
            default=declaration.default,
            for_model=declaration.for_model,
            for_node=declaration.for_node,
        )
        # Checked as its tree; sent as the BSX it was written in, when it was, which the
        # server parses into the same tree.
        checked = check_blok(BlokImplementationInput(components=(declaration.component,), **fields))
        declared_bloks[blok_key] = (
            BlokImplementationInput(source=declaration.source, **fields)
            if declaration.source is not None
            else checked
        )

    return declared_bloks


def demo_state_for(
    component: ComponentNodeInput,
    dependencies: Iterable[AgentDependencyInput],
    demo_state: Mapping[str, Any],
) -> dict[str, Any]:
    """``demo_state`` with the idle task records ``component`` reads added.

    Each name a call is bound to (``.into(run)``) and each action whose latest
    task the tree reads (``dep.action.latest``) starts as a call that has not run:
    what the renderer shows until it does. The server takes every top-level key
    as a path root, so a tree that reads one of these needs it here.
    """
    state = {**demo_state, **{name: idle_task_record() for name in bound_calls(component)}}
    for dependency_key, action_key in sorted(latest_task_reads(component, dependencies)):
        of_dependency = dict(state.get(dependency_key) or {})
        of_action = dict(of_dependency.get(action_key) or {})
        of_action.setdefault(LATEST_SEGMENT, idle_task_record())
        of_dependency[action_key] = of_action
        state[dependency_key] = of_dependency
    return state


class _ReferenceCollector(BlokVisitor):
    """Collects the dependency keys, actions and states a blok tree references."""

    def __init__(
        self,
        aliases: dict[str, str],
        local_roots: frozenset[str] = frozenset(),
        names_of: Callable[[str], tuple[Collection[str], Collection[str]]] | None = None,
    ) -> None:
        self.aliases = aliases
        # Per dependency key, the actions and the states it has to offer: what
        # tells ``self.blur.latest`` (a task) from ``self.stage.latest`` (a field).
        self.names_of = names_of
        # Roots of the blok's own UI state: they name no dependency.
        self.local_roots = local_roots
        # Names calls are bound to (``.into(name)``).
        self.bound: set[str] = set()
        self.actions: dict[str, set[str]] = {}
        self.states: dict[str, set[str]] = {}
        # A ``state.<key>`` reference that names no dependency; resolvable only
        # when the blok has exactly one dependency to attribute it to.
        self.implicit_states: set[str] = set()
        # ``assets.<dependency>.<name>`` references: (dependency key, asset name).
        self.assets: set[tuple[str, str]] = set()

    def canonical(self, key: str) -> str:
        return self.aliases.get(key, key)

    @property
    def dependency_keys(self) -> set[str]:
        return set(self.actions) | set(self.states)

    def visit_path(self, path: str, scope: dict[str, Any], context: str) -> None:
        path_parts = path.split(".")
        if not path_parts or path_parts[0] in scope:
            return

        root = path_parts[0]
        if root == "utils" or root == EVENT_SCOPE_KEY or root in self.local_roots:
            return

        if root == "state":
            if len(path_parts) >= 3:
                self.states.setdefault(self.canonical(path_parts[1]), set()).add(
                    path_parts[2]
                )
            elif len(path_parts) >= 2:
                self.implicit_states.add(path_parts[1])
            return

        named = asset_path_parts(path)
        if named is not None:
            dependency, name = named
            self.assets.add((self.canonical(dependency), name))
            # The asset is its dependency's: the key has to be one the blok maps
            # to an agent, even when nothing else of that agent is used.
            self.states.setdefault(self.canonical(dependency), set())
            return

        latest = latest_task_path_parts(path)
        if latest is not None and self.names_of is not None:
            dependency, name = self.canonical(latest[0]), latest[1]
            actions, states = self.names_of(dependency)
            if name in actions and name in states:
                raise ValueError(
                    f"'{path}' in {context} could be the latest task of the action '{name}' "
                    f"or the field '{LATEST_SEGMENT}' of the state '{name}'. Rename one of them."
                )
            if name in actions:
                # The latest task of an action is the action's: demanded like a call to it.
                self.actions.setdefault(dependency, set()).add(name)
                return

        if len(path_parts) >= 2:
            self.states.setdefault(self.canonical(root), set()).add(path_parts[1])

    def visit_agent_call(
        self, call: AgentProbeInput, scope: dict[str, Any], context: str
    ) -> None:
        self.actions.setdefault(self.canonical(call.dependency), set()).add(
            action_key_for(call)
        )

    def visit_component(
        self, node: ComponentNodeInput, scope: dict[str, Any], context: str
    ) -> None:
        # Components carry no references; only their props do.
        return None

    def visit_util_call(
        self, call: UtilCallInput, scope: dict[str, Any], context: str
    ) -> None:
        return None

    def declare_foreach_local(
        self, name: str, items_path: str, scope: dict[str, Any], context: str
    ) -> None:
        # The item's schema is only knowable once dependencies exist, which is
        # what this pass is building. Declaring the name is enough here.
        self.visit_path(items_path, scope, context)

    def declare_bound_call(self, name: str, call: AgentProbeInput, context: str) -> None:
        # In scope from here on, so a path rooted at it names no dependency.
        self.bound.add(name)


def _build_dependencies_for_component(
    component: ComponentNodeInput,
    app_registry: "AppRegistry",
    explicit_dependencies: Sequence[AgentDependencyInput],
    local_state: Mapping[str, Any] | None = None,
    demo_state: Mapping[str, Any] | None = None,
) -> list[AgentDependencyInput]:
    """Resolve a blok's dependencies, preferring explicitly declared ones.

    Dependencies referenced by the tree but not declared explicitly are inferred
    from the agent's *own* implementations and states. A dependency satisfied by
    another app cannot be inferred that way -- pass it in via
    ``register_blok(dependencies={"key": DeclaredProtocol})``.

    A root named in ``local_state`` is the blok's own UI state and no dependency.
    So is a ``demo_state`` key the tree never calls and that names none of this
    agent's states: every root would otherwise be read as this agent under
    another name.
    """
    explicit_by_key = {
        dependency.key: dependency for dependency in explicit_dependencies
    }
    aliases = {
        dependency.app: dependency.key
        for dependency in explicit_dependencies
        if dependency.app is not None
    }

    def names_of(dependency_key: str) -> tuple[Collection[str], Collection[str]]:
        explicit = explicit_by_key.get(dependency_key)
        if explicit is None:
            return app_registry.implementations.keys(), app_registry.states.keys()
        return (
            {demand.key for demand in explicit.action_dependencies or ()},
            {demand.key for demand in explicit.state_dependencies or ()},
        )

    collector = _ReferenceCollector(aliases, frozenset(local_state or ()), names_of)
    walk_component(component, collector)

    taken = (
        RESERVED_ROOTS
        | collector.local_roots
        | set(demo_state or ())
        | set(explicit_by_key)
        | collector.dependency_keys
    )
    clashing = sorted(collector.bound & taken)
    if clashing:
        raise ValueError(
            f"A call cannot be bound to {clashing}: the name is reserved, or already names "
            f"a dependency or the blok's own state."
        )

    for dependency, name in sorted(collector.assets):
        if dependency != SELF_KEY:
            raise ValueError(
                f"Blok names the asset '{name}' of '{dependency}'. Only an app's own "
                f"assets can be named for now: '@{SELF_KEY}.assets.<name>'."
            )
        if name not in app_registry.registered_assets:
            raise ValueError(
                f"Blok names the asset '{name}', which this app has not registered. "
                f"Registered assets: {sorted(app_registry.registered_assets)}"
            )

    called_locals = collector.local_roots & set(collector.actions)
    if called_locals:
        raise ValueError(
            f"Blok local state {sorted(called_locals)} is also the target of an agent "
            f"call. A root is either the blok's own state or a dependency, not both."
        )

    for key in set(demo_state or ()) - set(explicit_by_key) - RESERVED_ROOTS:
        referenced = collector.states.get(key)
        if referenced is None or key in collector.actions:
            continue
        if referenced.isdisjoint(app_registry.states):
            del collector.states[key]

    dependency_keys = collector.dependency_keys | set(explicit_by_key)

    if collector.implicit_states:
        if len(dependency_keys) != 1:
            raise ValueError(
                "Cannot resolve unscoped state references in blok without exactly one "
                f"explicit dependency key. Found dependencies: {sorted(dependency_keys)}"
            )

        dependency_key = next(iter(dependency_keys))
        collector.states.setdefault(dependency_key, set()).update(
            collector.implicit_states
        )

    dependencies: list[AgentDependencyInput] = []

    for dependency_key in sorted(dependency_keys):
        explicit = explicit_by_key.get(dependency_key)
        if explicit is not None:
            # The declared dependency already carries its own demands, resolved
            # against the protocol rather than this agent's implementations.
            dependencies.append(explicit)
            continue

        action_demands = tuple(
            _create_action_dependency(action_key, app_registry)
            for action_key in sorted(collector.actions.get(dependency_key, set()))
        )
        state_demands = tuple(
            _create_state_dependency(state_key, app_registry)
            for state_key in sorted(collector.states.get(dependency_key, set()))
        )

        dependencies.append(
            AgentDependencyInput(
                key=dependency_key,
                app=None,
                optional=False,
                auto_resolvable=False,
                action_dependencies=action_demands or None,
                state_dependencies=state_demands or None,
            )
        )

    return dependencies


def _create_action_dependency(
    action_key: str,
    app_registry: "AppRegistry",
) -> ActionDependencyInput:
    implementation = app_registry.implementations.get(action_key)
    if implementation is None:
        raise ValueError(
            f"Blok references unknown action '{action_key}'. It is not implemented "
            f"by this agent -- if another app provides it, declare the dependency "
            f"explicitly via register_blok(dependencies=[...])."
        )

    return build_action_dependency_input(
        key=action_key,
        definition=implementation.definition,
        action_key=action_key,
    )


def _create_state_dependency(
    state_key: str,
    app_registry: "AppRegistry",
) -> StateDependencyInput:
    state_implementation = app_registry.states.get(state_key)
    if state_implementation is None:
        raise ValueError(
            f"Blok references unknown state '{state_key}'. It is not provided "
            f"by this agent -- if another app provides it, declare the dependency "
            f"explicitly via register_blok(dependencies=[...])."
        )

    return build_state_dependency_input(
        key=state_key,
        state_key=state_key,
        definition=state_implementation.definition,
    )


def _autogenerate_demo_state(
    dependencies: Iterable[AgentDependencyInput],
    app_registry: "AppRegistry",
) -> dict[str, Any]:
    """Synthesize a placeholder value for every state the blok reads.

    Values come from the state's declared *ports*, not from instantiating the
    state class: a state with required fields cannot be constructed with no
    arguments, and its ports already describe the shape the renderer needs.

    A dependency whose states have no local definition (one satisfied by another
    app) is omitted entirely -- a partial entry would fail the demo_state
    completeness check on ``BlokImplementationInput``.
    """
    demo_state: dict[str, Any] = {}

    for dependency in dependencies:
        dependency_demo_state: dict[str, Any] = {}
        complete = True

        for state_demand in dependency.state_dependencies or ():
            state_implementation = app_registry.states.get(state_demand.key)
            if state_implementation is None:
                complete = False
                break

            dependency_demo_state[state_demand.key] = {
                port.key: _demo_value_for_port(port)
                for port in state_implementation.definition.ports or ()
            }

        if complete and dependency_demo_state:
            demo_state[dependency.key] = dependency_demo_state

    return demo_state


_DEMO_SCALARS: dict[PortKind, Any] = {
    PortKind.STRING: "",
    PortKind.INT: 0,
    PortKind.FLOAT: 0.0,
    PortKind.QUANTITY: 0.0,
    PortKind.BOOL: False,
    PortKind.LIST: [],
    PortKind.DICT: {},
}


def _demo_value_for_port(port: ReturnPortInput) -> Any:
    """A placeholder value matching a port's declared shape."""
    # Only arg ports carry a default.
    default = getattr(port, "default", None)
    if default is not None:
        return default

    if port.nullable:
        return None

    if port.kind == PortKind.ENUM:
        first_choice = next(iter(port.choices or ()), None)
        return first_choice.value if first_choice is not None else ""

    if port.kind in (PortKind.MODEL, PortKind.INTERFACE):
        return {child.key: _demo_value_for_port(child) for child in port.children or ()}

    return _DEMO_SCALARS.get(port.kind)
