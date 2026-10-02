"""Validate blok component trees against their declared dependencies."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from arkitekt_spec.actions import (
    AgentDependencyInput,
    AgentProbeInput,
    ComponentNodeInput,
    PortMatchInput,
    StateDependencyInput,
    StateImplementationInput,
    UtilCallInput,
)
from arkitekt_spec.declare.blok.walk import (
    FOREACH_COMPONENT,
    FOREACH_ITEMS_PROP,
    BlokVisitor,
    action_key_for,
    walk_component,
)
from arkitekt_spec.declare.catalogs import (
    CatalogView,
    Diagnostic,
    check_call,
    check_component,
)
from arkitekt_spec.declare.definition.match import build_port_matches


@dataclass(frozen=True)
class DependencyIndex:
    """Everything a blok reference can resolve against, derived once per blok."""

    keys: frozenset[str]
    aliases: dict[str, str]
    state_demands: dict[str, dict[str, StateDependencyInput]]
    state_index: dict[str, list[tuple[str, StateDependencyInput]]]
    action_demands: dict[str, frozenset[str] | None]
    # What each declared action returns, where its demand says so.
    action_returns: dict[str, dict[str, tuple[PortMatchInput, ...] | None]]

    @classmethod
    def from_dependencies(
        cls, dependencies: Iterable[AgentDependencyInput]
    ) -> "DependencyIndex":
        dependencies = list(dependencies)
        state_index: dict[str, list[tuple[str, StateDependencyInput]]] = {}
        for dependency in dependencies:
            for state_demand in dependency.state_dependencies or ():
                state_index.setdefault(state_demand.key, []).append(
                    (dependency.key, state_demand)
                )

        return cls(
            keys=frozenset(dependency.key for dependency in dependencies),
            aliases={
                dependency.app: dependency.key
                for dependency in dependencies
                if dependency.app is not None
            },
            state_demands={
                dependency.key: {
                    state_demand.key: state_demand
                    for state_demand in dependency.state_dependencies or ()
                }
                for dependency in dependencies
            },
            state_index=state_index,
            action_demands={
                dependency.key: (
                    frozenset(
                        action_demand.key
                        for action_demand in dependency.action_dependencies
                    )
                    if dependency.action_dependencies is not None
                    else None
                )
                for dependency in dependencies
            },
            action_returns={
                dependency.key: {
                    action_demand.key: (
                        action_demand.demand.return_matches if action_demand.demand else None
                    )
                    for action_demand in dependency.action_dependencies or ()
                }
                for dependency in dependencies
            },
        )

    def canonical(self, key: str) -> str:
        """Resolve an app alias to the dependency key it was registered under."""
        return self.aliases.get(key, key)


# Path roots the blok language reads itself; ``self`` is this agent's own key.
# None of them can name a blok's own UI state.
RESERVED_ROOTS = frozenset({"self", "state", "actions", "utils"})


def idle_task_record() -> dict[str, Any]:
    """The value a bound call's name holds before the call has run.

    The one place the record's shape is written down: :func:`_task_record_match`
    is derived from it, and the renderer writes the same fields as the task runs.
    """
    return {
        "status": "idle",
        "running": False,
        "done": False,
        "failed": False,
        "progress": 0,
        "message": "",
        "error": "",
        "result": None,
        "returns": {},
    }


def _task_record_match(
    name: str, returns: tuple[PortMatchInput, ...] | None
) -> PortMatchInput:
    """The shape of a bound call's record: its fixed fields, and what the action returns.

    ``result`` is the action's first return, ``returns`` all of them by port key.
    An action whose returns are not declared leaves both untyped: they can be
    read, but not looked into.
    """
    fields = [
        PortMatchInput(key=key)
        for key in idle_task_record()
        if key not in ("result", "returns")
    ]
    result = returns[0].model_copy(update={"key": "result"}) if returns else PortMatchInput(key="result")
    return PortMatchInput(
        key=name,
        children=(*fields, result, PortMatchInput(key="returns", children=returns)),
    )


def local_roots_of(
    dependencies: Iterable[AgentDependencyInput] | None,
    demo_state: Any,
) -> frozenset[str]:
    """The roots of a blok's own UI state: the ``demo_state`` keys no dependency owns.

    The same rule the server applies: a path may be rooted at a dependency key or
    at any top-level ``demo_state`` key. One that is not a dependency is state the
    blok keeps for itself -- a form's values -- and has no schema to resolve against.
    """
    if not isinstance(demo_state, Mapping):
        return frozenset()
    owned = {dependency.key for dependency in dependencies or ()} | RESERVED_ROOTS
    keys: Iterable[Any] = demo_state  # pyright: ignore[reportUnknownVariableType]
    return frozenset(str(key) for key in keys) - owned


class _ValidationVisitor(BlokVisitor):
    """Resolves every reference in a blok against its declared dependencies."""

    def __init__(
        self,
        index: DependencyIndex,
        catalog: "CatalogView | None" = None,
        local_roots: frozenset[str] = frozenset(),
    ) -> None:
        self.index = index
        self.catalog = catalog
        self.local_roots = local_roots
        self.diagnostics: list[Diagnostic] = []

    def visit_component(
        self, node: ComponentNodeInput, scope: dict[str, PortMatchInput | None], context: str
    ) -> None:
        """Check the node against the catalog in force, if any is known."""
        if self.catalog is None:
            return
        check_component(
            node.component,
            [prop.key for prop in node.props or ()],
            bool(node.children),
            {
                prop.key: prop.agent_call is not None or prop.util_call is not None
                for prop in node.props or ()
            },
            self.catalog,
            context,
        )

    def visit_path(
        self, path: str, scope: dict[str, PortMatchInput | None], context: str
    ) -> None:
        self._resolve(path, scope, context)

    def visit_agent_call(
        self, call: AgentProbeInput, scope: dict[str, PortMatchInput | None], context: str
    ) -> None:
        dependency = self.index.canonical(call.dependency)
        if dependency not in self.index.keys:
            raise ValueError(
                f"Unknown dependency '{call.dependency}' in agent call in {context}. "
                f"Available dependencies: "
                f"{sorted(self.index.keys | set(self.index.aliases))}"
            )

        # Only checkable when the dependency enumerates its actions; a
        # dependency with no declared action demands accepts any operation.
        declared_actions = self.index.action_demands.get(dependency)
        action_key = action_key_for(call)
        if declared_actions is not None and action_key not in declared_actions:
            raise ValueError(
                f"Unknown action '{action_key}' on dependency '{call.dependency}' "
                f"in {context}. Available actions: {sorted(declared_actions)}"
            )

    def visit_util_call(
        self, call: UtilCallInput, scope: dict[str, PortMatchInput | None], context: str
    ) -> None:
        """Resolve the operation against the catalog in force, if any is known.

        Nested util calls inside arguments arrive here on their own, because
        :func:`walk_component` recurses through the argument tree.
        """
        if self.catalog is None:
            return
        finding = check_call(
            call.operation,
            [argument.key for argument in call.arguments or () if argument.key is not None],
            self.catalog,
            context,
        )
        if finding is not None:
            self.diagnostics.append(finding)
        return

    def declare_foreach_local(
        self,
        name: str,
        items_path: str,
        scope: dict[str, PortMatchInput | None],
        context: str,
    ) -> PortMatchInput | None:
        return _infer_iterable_item_match(
            self._resolve(items_path, scope, context), items_path
        )

    def declare_bound_call(
        self, name: str, call: AgentProbeInput, context: str
    ) -> PortMatchInput:
        dependency = self.index.canonical(call.dependency)
        returns = self.index.action_returns.get(dependency, {}).get(action_key_for(call))
        return _task_record_match(name, returns)

    def _resolve(
        self, path: str, scope: dict[str, PortMatchInput | None], context: str
    ) -> PortMatchInput | None:
        index = self.index
        path_parts = path.split(".")
        root = path_parts[0]

        if root in scope:
            return _resolve_port_match_path(scope[root], path_parts[1:], path, context)

        canonical_root = index.canonical(root)

        if canonical_root in index.state_demands:
            return _resolve_dependency_state_path(
                canonical_root,
                path_parts[1:],
                index.state_demands,
                path,
                context,
            )

        if root in self.local_roots:
            # The blok's own UI state: nothing declares its shape, so the path
            # is taken as written.
            return None

        if root == "state" and len(path_parts) > 1:
            canonical_state_dependency = index.canonical(path_parts[1])
            if len(path_parts) > 2 and canonical_state_dependency in index.state_demands:
                return _resolve_dependency_state_path(
                    canonical_state_dependency,
                    path_parts[2:],
                    index.state_demands,
                    path,
                    context,
                )

            state_key = path_parts[1]
            matching_state_demands = index.state_index.get(state_key, [])
            if len(matching_state_demands) == 1:
                _, state_demand = matching_state_demands[0]
                return _resolve_port_match_path(
                    _state_demand_root_match(state_demand),
                    path_parts[2:],
                    path,
                    context,
                )
            if len(matching_state_demands) > 1:
                raise ValueError(
                    f"Ambiguous state reference '{path}' in {context}. "
                    f"State '{state_key}' exists on multiple dependencies. "
                    f"Use 'state.<dependency>.{state_key}...' or '<dependency>.{state_key}...'."
                )

        if root == "actions" and len(path_parts) > 1:
            if index.canonical(path_parts[1]) in index.keys:
                return None

        if canonical_root in index.keys:
            return None

        if root == "utils":
            return None

        raise ValueError(
            f"Unknown non-static reference '{path}' in {context}. "
            f"Available locals: {sorted(scope)}. "
            f"Available dependencies: {sorted(index.keys)}. "
            f"Available state values: {sorted(index.state_index)}"
        )


def validate_blok(
    component: ComponentNodeInput,
    dependencies: list[AgentDependencyInput],
    catalog: "CatalogView | None" = None,
    local_roots: Iterable[str] = (),
) -> list[Diagnostic]:
    """Validate every reference in ``component`` resolves against ``dependencies``.

    A path rooted at one of ``local_roots`` is the blok's own UI state (see
    :func:`local_roots_of`) and resolves without a schema.

    With a ``catalog``, the tree's components, props and util operations are checked
    against it too. Without one, only references are checked -- component names are
    unknowable, exactly as on the server before a UI app registers any.

    Args:
        component: The component tree to validate.
        dependencies: What references in the tree may resolve against.
        catalog: The catalog view in force, or ``None`` to skip every catalog rule.
        local_roots: Path roots that name the blok's own UI state.

    Returns:
        The non-fatal findings, e.g. util calls naming an operation the catalog does
        not provide. Empty when nothing was found or no catalog was given.

    Raises:
        ValueError: If any reference cannot be resolved, or any catalog rule is broken.
            The message names the component and its structural id.
    """
    index = DependencyIndex.from_dependencies(dependencies)
    visitor = _ValidationVisitor(index, catalog, frozenset(local_roots) - index.keys)
    walk_component(component, visitor)
    return visitor.diagnostics


class _CatalogVisitor(BlokVisitor):
    """Checks a tree against a catalog alone, ignoring every reference.

    References need the blok's fully inferred dependencies, which only exist once
    :func:`~rekuest.blok.registry.build_declared_bloks` has run. Catalog rules need
    none, so they can fire the moment a blok is declared.
    """

    def __init__(self, catalog: CatalogView) -> None:
        self.catalog = catalog
        self.diagnostics: list[Diagnostic] = []

    def visit_component(
        self, node: ComponentNodeInput, scope: dict[str, PortMatchInput | None], context: str
    ) -> None:
        check_component(
            node.component,
            [prop.key for prop in node.props or ()],
            bool(node.children),
            {
                prop.key: prop.agent_call is not None or prop.util_call is not None
                for prop in node.props or ()
            },
            self.catalog,
            context,
        )

    def visit_util_call(
        self, call: UtilCallInput, scope: dict[str, PortMatchInput | None], context: str
    ) -> None:
        finding = check_call(
            call.operation,
            [argument.key for argument in call.arguments or () if argument.key is not None],
            self.catalog,
            context,
        )
        if finding is not None:
            self.diagnostics.append(finding)

    def visit_path(
        self, path: str, scope: dict[str, PortMatchInput | None], context: str
    ) -> None:
        return None

    def visit_agent_call(
        self, call: AgentProbeInput, scope: dict[str, PortMatchInput | None], context: str
    ) -> None:
        return None

    def declare_foreach_local(
        self,
        name: str,
        items_path: str,
        scope: dict[str, PortMatchInput | None],
        context: str,
    ) -> PortMatchInput | None:
        return None

    def declare_bound_call(
        self, name: str, call: AgentProbeInput, context: str
    ) -> PortMatchInput | None:
        return None


def validate_blok_catalog(
    component: ComponentNodeInput, catalog: CatalogView
) -> list[Diagnostic]:
    """Check ``component``'s components, props and util operations against ``catalog``.

    References are not checked -- they need dependencies this stage does not have.

    Returns:
        The non-fatal findings, e.g. operations the catalog does not provide.

    Raises:
        ValueError: If a catalog rule is broken. The message names the component
            and its structural id.
    """
    visitor = _CatalogVisitor(catalog)
    walk_component(component, visitor)
    return visitor.diagnostics


def _resolve_dependency_state_path(
    dependency_key: str,
    path_parts: list[str],
    dependency_state_demands: dict[str, dict[str, StateDependencyInput]],
    path: str,
    context: str,
) -> PortMatchInput | None:
    if not path_parts:
        return None

    state_key = path_parts[0]
    state_demand = dependency_state_demands.get(dependency_key, {}).get(state_key)
    if state_demand is None:
        available_state_keys = sorted(dependency_state_demands.get(dependency_key, {}))
        raise ValueError(
            f"Unknown nested reference '{path}' in {context}: state '{state_key}' "
            f"does not exist on dependency '{dependency_key}'. "
            f"Available states: {available_state_keys}"
        )

    return _resolve_port_match_path(
        _state_demand_root_match(state_demand),
        path_parts[1:],
        path,
        context,
    )


def _state_demand_root_match(state_demand: StateDependencyInput) -> PortMatchInput:
    return PortMatchInput(
        key=state_demand.key,
        children=state_demand.demand.matches if state_demand.demand else None,
    )


def _resolve_port_match_path(
    port_match: PortMatchInput | None,
    path_parts: list[str],
    path: str,
    context: str,
) -> PortMatchInput | None:
    current_match = port_match
    remaining_parts = list(path_parts)

    if current_match is None:
        if remaining_parts:
            raise ValueError(
                f"Unknown nested reference '{path}' in {context}: no schema is available "
                f"to validate '{remaining_parts[0]}'"
            )
        return None

    while remaining_parts:
        next_part = remaining_parts.pop(0)
        children = current_match.children or ()

        if not children:
            raise ValueError(
                f"Unknown nested reference '{path}' in {context}: '{next_part}' does not exist"
            )

        child_match = next(
            (child for child in children if child.key == next_part), None
        )
        if child_match is None and len(children) == 1 and children[0].key == "...":
            child_match = children[0]

        if child_match is None:
            available_keys = sorted(
                child.key
                for child in children
                if child.key is not None and child.key != "..."
            )
            raise ValueError(
                f"Unknown nested reference '{path}' in {context}: '{next_part}' does not exist. "
                f"Available keys: {available_keys}"
            )

        current_match = child_match

    return current_match


def _infer_iterable_item_match(
    port_match: PortMatchInput | None,
    path: str,
) -> PortMatchInput | None:
    if port_match is None:
        return None

    children = port_match.children or ()
    if len(children) == 1 and children[0].key == "...":
        return children[0]

    raise ValueError(
        f"{FOREACH_COMPONENT} '{FOREACH_ITEMS_PROP}' reference '{path}' must resolve "
        f"to a list-like value"
    )


def resolve_state_reference(
    dependency: str | None,
    state_path: str,
    *,
    dependencies: Iterable[AgentDependencyInput],
    own_states: Iterable[StateImplementationInput],
    context: str,
) -> None:
    """Validate a ``withStateChoices`` state reference is resolvable.

    A ``dependency`` of ``None`` denotes a ``self`` reference which is resolved
    against the agent's own ``own_states``. A named ``dependency`` is resolved
    through that dependency's declared ``state_demands``.

    Raises:
        ValueError: If the reference cannot be resolved.
    """
    path_parts = [part for part in state_path.split(".") if part]
    if not path_parts:
        return

    if dependency is None:
        own_by_interface = {state.interface: state for state in own_states}
        state_key = path_parts[0]
        state = own_by_interface.get(state_key)
        if state is None:
            raise ValueError(
                f"Unknown self state reference '{state_path}' in {context}: "
                f"no own state '{state_key}'. "
                f"Available states: {sorted(own_by_interface)}"
            )
        root_match = PortMatchInput(
            key=state_key,
            children=build_port_matches(state.definition.ports),
        )
        _resolve_port_match_path(root_match, path_parts[1:], state_path, context)
        return

    dependency_state_demands = DependencyIndex.from_dependencies(
        dependencies
    ).state_demands
    if dependency not in dependency_state_demands:
        raise ValueError(
            f"State choice widget in {context} references unknown dependency "
            f"'{dependency}'. Available dependencies: {sorted(dependency_state_demands)}"
        )

    _resolve_dependency_state_path(
        dependency,
        path_parts,
        dependency_state_demands,
        state_path,
        context,
    )
