"""Validate blok component trees against their declared dependencies."""

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
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
from arkitekt_spec.declare.blok.parser import bsx
from arkitekt_spec.declare.blok.walk import (
    FOREACH_COMPONENT,
    FOREACH_ITEMS_PROP,
    BlokVisitor,
    action_key_for,
    foreach_parts,
    node_context,
    walk_component,
)
from arkitekt_spec.declare.catalogs import (
    BLOK_ARGS_ROOT,
    CONTEXT_ROOTS,
    HTML_CONTEXT,
    LATEST_SEGMENT,
    LOGO_MAX_DEPTH,
    LOGO_MAX_FOREACH_ITEMS,
    LOGO_MAX_NODES,
    LOGO_MAX_PATH_DATA,
    LOGO_PAINT_PROPS,
    LOGO_PAINT_TOKENS,
    LOGO_PATH_DATA_PROPS,
    STANDARD_CATALOG_NAME,
    BlokCheckMode,
    Catalog,
    CatalogView,
    Diagnostic,
    asset_path_parts,
    base_only_view,
    base_version_named,
    check_call,
    check_component,
    check_placement,
    latest_task_path_parts,
    load_standard_catalog,
    parse_ui_catalog,
    resolve_catalogs,
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
RESERVED_ROOTS = frozenset({"self", "state", "actions", "utils", "event"})

# What a handler's ``event`` is on the wire: the renderer's scope key for the
# value that triggered the call. Nothing declares its shape.
EVENT_SCOPE_KEY = "$event"


def _bound_paths(node: ComponentNodeInput) -> dict[str, str]:
    """Per prop key, the path a prop of ``node`` is bound to."""
    return {
        prop.key: prop.dynamic_value.path
        for prop in node.props or ()
        if prop.dynamic_value is not None and prop.dynamic_value.path is not None
    }


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


def latest_task_read(index: DependencyIndex, path: str) -> tuple[str, str, list[str]] | None:
    """The ``(dependency key, action key, rest)`` of a path that reads an action's latest task.

    ``<dependency>.<action>.latest`` is the task the action last ran as: the same
    record a bound call holds, without the blok having started the run. It is one
    only where the dependency demands that action; a demanded state of the same
    name is read as the state it always was.
    """
    named = latest_task_path_parts(path)
    if named is None:
        return None
    dependency = index.canonical(named[0])
    if named[1] not in index.action_returns.get(dependency, {}):
        return None
    if named[1] in index.state_demands.get(dependency, {}):
        return None
    return dependency, named[1], named[2]


def latest_task_reads(
    component: ComponentNodeInput, dependencies: Iterable[AgentDependencyInput]
) -> set[tuple[str, str]]:
    """Every ``(dependency key, action key)`` whose latest task the tree reads."""
    index = DependencyIndex.from_dependencies(dependencies)
    found: set[tuple[str, str]] = set()

    class _Reads(BlokVisitor):
        def visit_path(self, path: str, scope: dict[str, Any], context: str) -> None:
            if path.split(".")[0] in scope:
                return
            read = latest_task_read(index, path)
            if read is not None:
                found.add(read[:2])

        def visit_component(self, node: ComponentNodeInput, scope: dict[str, Any], context: str) -> None:
            return None

        def visit_agent_call(self, call: AgentProbeInput, scope: dict[str, Any], context: str) -> None:
            return None

        def visit_util_call(self, call: UtilCallInput, scope: dict[str, Any], context: str) -> None:
            return None

        def declare_foreach_local(self, name: str, items_path: str, scope: dict[str, Any], context: str) -> None:
            self.visit_path(items_path, scope, context)

        def declare_bound_call(self, name: str, call: AgentProbeInput, context: str) -> None:
            return None

    walk_component(component, _Reads())
    return found


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
        mode: BlokCheckMode = "warning",
    ) -> None:
        self.index = index
        self.catalog = catalog
        self.local_roots = local_roots
        self.diagnostics: list[Diagnostic] = []
        # Where a catalog finding goes: the diagnostics, or raised when strict.
        self.findings = None if mode == "strict" else self.diagnostics

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
            _bound_paths(node),
            self.findings,
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
            self.findings,
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

        if root == EVENT_SCOPE_KEY:
            return None

        canonical_root = index.canonical(root)

        if root not in self.local_roots and canonical_root in index.keys:
            # A dependency's file, not one of its values: which asset it is, and
            # whether the app has it, is checked where the blok's dependencies are built.
            try:
                if asset_path_parts(path) is not None:
                    return None
            except ValueError as error:
                raise ValueError(f"{context}: {error}") from None

        if root not in self.local_roots and canonical_root in index.keys:
            latest = latest_task_read(index, path)
            if latest is not None:
                _, action, rest = latest
                returns = index.action_returns[canonical_root][action]
                return _resolve_port_match_path(
                    _task_record_match(LATEST_SEGMENT, returns), rest, path, context
                )

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
    mode: BlokCheckMode = "warning",
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
        mode: ``"warning"`` returns a broken catalog rule as a finding, ``"strict"``
            raises it (see :data:`~arkitekt_spec.declare.catalogs.BlokCheckMode`).

    Returns:
        The non-fatal findings, e.g. util calls naming an operation the catalog does
        not provide. Empty when nothing was found or no catalog was given.

    Raises:
        ValueError: If any reference cannot be resolved, or, in strict mode, any
            catalog rule is broken. The message names the component and its
            structural id.
    """
    index = DependencyIndex.from_dependencies(dependencies)
    visitor = _ValidationVisitor(index, catalog, frozenset(local_roots) - index.keys, mode)
    walk_component(component, visitor)
    if catalog is not None:
        _check_tree_placement(component, catalog, visitor.findings)
    return visitor.diagnostics


def check_context_root(node: ComponentNodeInput, context: str) -> None:
    """The root of a scene blok is a ``Scene``, of a logo blok an ``SVG``, whatever catalog is in force.

    Args:
        node: The blok's root.
        context: The blok's context, as a ``ComponentContext`` value. An html blok's root
            may be anything.

    Raises:
        ValueError: If the root is not the one the context requires.
    """
    context = getattr(context, "value", context)
    required = CONTEXT_ROOTS.get(context)
    if required is not None and node.component != required:
        raise ValueError(
            f"{node_context(node)}: the root of a {context.lower()} blok must be a {required!r}, "
            f"not {node.component!r}"
        )


def check_args_reads(node: ComponentNodeInput, arg_keys: Iterable[str]) -> None:
    """Every ``@args.<key>`` the tree reads names one of the blok's declared args.

    ``args`` is a local root, so nothing else checks what is read below it; a ``foreach``
    local of the same name shadows it.

    Raises:
        ValueError: If a path reads an arg the blok does not declare, or reads ``args`` whole.
    """
    visitor = _ArgsVisitor(frozenset(arg_keys))
    walk_component(node, visitor)


class _ArgsVisitor(BlokVisitor):
    """Checks the second segment of every path rooted at ``args``."""

    def __init__(self, arg_keys: frozenset[str]) -> None:
        self.arg_keys = arg_keys

    def visit_component(self, node: ComponentNodeInput, scope: dict[str, Any], context: str) -> None:
        return None

    def visit_path(self, path: str, scope: dict[str, Any], context: str) -> None:
        parts = path.replace("/", ".").split(".")
        if parts[0] != BLOK_ARGS_ROOT or BLOK_ARGS_ROOT in scope:
            return
        if len(parts) < 2:
            raise ValueError(f"{context} reads @{BLOK_ARGS_ROOT} whole; read one arg, e.g. @{BLOK_ARGS_ROOT}.<key>")
        if parts[1] not in self.arg_keys:
            declared = ", ".join(sorted(self.arg_keys)) or "none"
            raise ValueError(f"{context} reads @{BLOK_ARGS_ROOT}.{parts[1]}, but the blok declares no such arg (declared: {declared})")

    def visit_agent_call(self, call: AgentProbeInput, scope: dict[str, Any], context: str) -> None:
        return None

    def visit_util_call(self, call: UtilCallInput, scope: dict[str, Any], context: str) -> None:
        return None

    def declare_foreach_local(self, name: str, items_path: str, scope: dict[str, Any], context: str) -> Any:
        return None

    def declare_bound_call(self, name: str, call: AgentProbeInput, context: str) -> Any:
        return None


def check_logo_complexity(node: ComponentNodeInput, demo_state: Mapping[str, Any] | None = None) -> None:
    """A logo is an icon: few nodes, shallow, little path data, and loops over few items.

    A ``foreach`` is held to the length of the list it reads in ``demo_state``, which is
    the only state a logo is drawn with; one whose list is not there draws nothing.

    Raises:
        ValueError: If the tree passes any of the ``LOGO_MAX_*`` limits.
    """
    nodes = 0
    path_data = 0

    def visit(current: ComponentNodeInput, depth: int) -> None:
        nonlocal nodes, path_data
        nodes += 1
        if depth > LOGO_MAX_DEPTH:
            raise ValueError(f"{node_context(current)}: a logo nests at most {LOGO_MAX_DEPTH} deep")
        for prop in current.props or ():
            if prop.key in LOGO_PATH_DATA_PROPS and isinstance(prop.static_value, str):
                path_data += len(prop.static_value)
            if current.component == "foreach" and prop.key == "items":
                items = _demo_items(prop, demo_state)
                if items > LOGO_MAX_FOREACH_ITEMS:
                    raise ValueError(
                        f"{node_context(current)}: a foreach in a logo draws at most "
                        f"{LOGO_MAX_FOREACH_ITEMS} items, its demo state has {items}"
                    )
        for child in current.children or ():
            visit(child, depth + 1)

    visit(node, 1)
    if nodes > LOGO_MAX_NODES:
        raise ValueError(f"{node_context(node)}: a logo has at most {LOGO_MAX_NODES} components, this one {nodes}")
    if path_data > LOGO_MAX_PATH_DATA:
        raise ValueError(
            f"{node_context(node)}: a logo holds at most {LOGO_MAX_PATH_DATA} characters of path data, "
            f"this one {path_data}"
        )


def check_logo_palette(node: ComponentNodeInput, demo_state: Mapping[str, Any] | None = None) -> None:
    """A logo paints only with the palette: ``LOGO_PAINT_TOKENS`` or a ``url(#id)``.

    A static paint is checked as written. A bound one is checked as the demo state
    resolves it, since that is the only state a logo is drawn with: inside a ``foreach``,
    a path through its ``let`` is resolved for every item. A path the demo state does not
    hold draws nothing and is not checked.

    Raises:
        ValueError: If a paint prop is a colour of its own, such as ``#fbbf24``.
    """

    def visit(current: ComponentNodeInput, scope: Mapping[str, list[Any]]) -> None:
        for prop in current.props or ():
            if prop.key not in LOGO_PAINT_PROPS:
                continue
            if prop.static_value is not None:
                values: list[Any] = [prop.static_value]
            elif prop.dynamic_value is not None and prop.dynamic_value.path:
                values = _resolve(prop.dynamic_value.path, demo_state, scope)
            else:
                values = []
            for value in values:
                if not is_logo_paint(value):
                    raise ValueError(
                        f"{node_context(current)}: a logo paints with "
                        f"{'|'.join(LOGO_PAINT_TOKENS)}|url(#…), not {value!r} in {prop.key!r}"
                    )
        inner = scope
        let_name, items_path = foreach_parts(current)
        if let_name and items_path:
            items = [item for found in _resolve(items_path, demo_state, scope) if isinstance(found, list) for item in found]
            inner = {**scope, let_name: items}
        for child in current.children or ():
            visit(child, inner)

    visit(node, {})


def is_logo_paint(value: Any) -> bool:
    """A palette token, or a ``url(#id)`` reference to a gradient or clip path."""
    if not isinstance(value, str):
        return False
    paint = value.strip()
    return paint in LOGO_PAINT_TOKENS or (paint.startswith("url(#") and paint.endswith(")"))


def _resolve(path: str, demo_state: Mapping[str, Any] | None, scope: Mapping[str, list[Any]]) -> list[Any]:
    """What ``path`` holds: one value from the demo state, or one per item of a ``foreach``'s ``let``."""
    segments = [segment for segment in path.replace("/", ".").split(".") if segment]
    if not segments:
        return []
    if segments[0] in scope:
        roots, segments = list(scope[segments[0]]), segments[1:]
    else:
        roots = [demo_state or {}]
    found: list[Any] = []
    for value in roots:
        for segment in segments:
            if not isinstance(value, Mapping) or segment not in value:
                break
            value = value[segment]
        else:
            found.append(value)
    return found


def _demo_items(prop: Any, demo_state: Mapping[str, Any] | None) -> int:
    """How many items a foreach's ``items`` gives in the demo state; 0 when it names none."""
    if isinstance(prop.static_value, list):
        return len(prop.static_value)
    path = prop.dynamic_value.path if prop.dynamic_value is not None else None
    value: Any = demo_state or {}
    for segment in (path or "").replace("/", ".").split("."):
        if not segment:
            continue
        if not isinstance(value, Mapping) or segment not in value:
            return 0
        value = value[segment]
    return len(value) if isinstance(value, list) else 0


def _check_tree_placement(
    node: ComponentNodeInput,
    catalog: CatalogView,
    findings: list[Diagnostic] | None,
    ancestors: tuple[str, ...] = (),
    context: str = HTML_CONTEXT,
) -> None:
    """Check every node's placement, carrying its ancestors and the context in force."""
    inner = check_placement(node.component, ancestors, context, catalog, node_context(node), findings)
    for child in node.children or ():
        _check_tree_placement(child, catalog, findings, (*ancestors, node.component), inner)


class _CatalogVisitor(BlokVisitor):
    """Checks a tree against a catalog alone, ignoring every reference.

    References need the blok's fully inferred dependencies, which only exist once
    :func:`~rekuest.blok.registry.build_declared_bloks` has run. Catalog rules need
    none, so they can fire the moment a blok is declared.
    """

    def __init__(self, catalog: CatalogView, mode: BlokCheckMode) -> None:
        self.catalog = catalog
        self.diagnostics: list[Diagnostic] = []
        self.findings = None if mode == "strict" else self.diagnostics

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
            _bound_paths(node),
            self.findings,
        )

    def visit_util_call(
        self, call: UtilCallInput, scope: dict[str, PortMatchInput | None], context: str
    ) -> None:
        finding = check_call(
            call.operation,
            [argument.key for argument in call.arguments or () if argument.key is not None],
            self.catalog,
            context,
            self.findings,
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
    component: ComponentNodeInput, catalog: CatalogView, mode: BlokCheckMode = "warning"
) -> list[Diagnostic]:
    """Check ``component``'s components, props and util operations against ``catalog``.

    References are not checked -- they need dependencies this stage does not have.

    Returns:
        The findings: operations the catalog does not provide and, unless ``mode`` is
        ``"strict"``, every broken catalog rule.

    Raises:
        ValueError: In strict mode, if a catalog rule is broken. The message names the
            component and its structural id.
    """
    visitor = _CatalogVisitor(catalog, mode)
    walk_component(component, visitor)
    _check_tree_placement(component, catalog, visitor.findings)
    return visitor.diagnostics


CatalogSource = str | Path | Catalog | Mapping[str, Any]
"""A catalog to check against: ``"orkestrator"`` (the vendored standard one), ``"base"``,
the path of a ``blok-catalog.json`` as a UI app exports it, that payload already loaded,
or a :class:`~arkitekt_spec.declare.catalogs.Catalog`."""


def _load_catalog(source: CatalogSource) -> Catalog | None:
    """The catalog ``source`` names, or ``None`` for the base catalog every view has anyway."""
    if isinstance(source, Catalog):
        return source
    if isinstance(source, Mapping):
        return parse_ui_catalog(dict(source))
    if isinstance(source, str) and source == STANDARD_CATALOG_NAME:
        return load_standard_catalog()
    if isinstance(source, str) and base_version_named(source) is not None:
        return None
    path = Path(source)
    if not path.is_file():
        raise ValueError(
            f"catalog {str(source)!r} is neither {STANDARD_CATALOG_NAME!r}, 'base', nor a blok-catalog.json file"
        )
    return parse_ui_catalog(json.loads(path.read_text(encoding="utf-8")))


def catalogs_view(catalogs: Sequence[CatalogSource] | None = None) -> CatalogView:
    """The view a blok is checked against when it is rendered by ``catalogs``.

    No catalogs means the standard one. Only the base catalog means base operations alone,
    which cannot tell a typo from an operation a UI provides, so nothing more is checked.
    """
    loaded = [catalog for source in catalogs or (STANDARD_CATALOG_NAME,) if (catalog := _load_catalog(source))]
    if not loaded:
        return base_only_view()
    return resolve_catalogs(loaded, authoritative=True)


def validate_bsx(
    source: str | ComponentNodeInput,
    catalogs: Sequence[CatalogSource] | None = None,
    *,
    mode: BlokCheckMode = "warning",
) -> list[Diagnostic]:
    """Check a blok against the catalogs that render it, on its own, with no app.

    Only the catalog rules run: components, props, children, placement and util
    operations. References need the dependencies an app infers, so
    :func:`validate_blok` checks those.

    Example:
        >>> validate_bsx('<Card><Button label="Go" /></Card>', ["orkestrator"])
        []

    Args:
        source: The blok, as BSX or already parsed.
        catalogs: What renders it (see :data:`CatalogSource`); the standard catalog when
            left out.
        mode: ``"warning"`` returns what is wrong; ``"strict"`` raises the first of it.

    Returns:
        The findings, one per broken rule. Empty when the blok checks out.

    Raises:
        ValueError: If the BSX does not parse, a catalog cannot be loaded or two of them
            conflict, or, in strict mode, a catalog rule is broken.
    """
    component = bsx(source) if isinstance(source, str) else source
    return validate_blok_catalog(component, catalogs_view(catalogs), mode)


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
