"""Turn an app's blok declarations into the inputs its agent uploads.

An agent's blok reads its own agent as ``self``: the states it reads (``self.arm.joints``) and
the actions it calls or watches (``self.move(...)``, ``self.tasks.move``) become the demands
of the one dependency it has, ``self``, built against this app's own declarations. Its
queries are checked against their services' schemas where the app knows them, which also
types their values. Then the whole tree is validated against all of it.
"""

from collections.abc import Mapping, Sequence
from dataclasses import replace as dataclass_replace
from typing import TYPE_CHECKING

from arkitekt_spec.actions import (
    ActionDependencyInput,
    AgentDependencyInput,
    AgentProbeInput,
    BlokDemoInput,
    BlokImplementationInput,
    BlokLocalInput,
    BlokQueryInput,
    BlokSubscriptionInput,
    ComponentContext,
    ComponentNodeInput,
    ComponentPropInput,
    PortKind,
    PortMatchInput,
    ReturnPortInput,
    StateDependencyInput,
    UtilCallInput,
)
from arkitekt_spec.blok.catalogs import BASE_NAME, ORKESTRATOR_MODULE, BlokView
from arkitekt_spec.blok.vocabulary import JSON, BlokError, Finding, load_vocabulary
from arkitekt_spec.declare.blok.graphql import check_query, graphql_available
from arkitekt_spec.declare.blok.infer import ArgPorts, resolve_local, resolve_scope
from arkitekt_spec.declare.blok.origin import DeclaredAt, finding_key, warn_at
from arkitekt_spec.declare.blok.validate import BlokScope, check_blok_tree
from arkitekt_spec.declare.blok.walk import (
    Where,
    action_key_for,
    argument_paths,
    iter_nodes,
    walk_tree,
)
from arkitekt_spec.declare.catalogs import BlokCheckMode
from arkitekt_spec.declare.definition.checks import check_blok
from arkitekt_spec.declare.definition.dependencies import (
    build_action_dependency_input,
    build_state_dependency_input,
)

if TYPE_CHECKING:
    from arkitekt_spec.declare.app import AppRegistry

SELF_KEY = "self"
"""The dependency key a blok's own agent goes by."""


class _SelfReads:
    """What a tree (and its query variables) reads of ``self``: states, actions, assets."""

    def __init__(self) -> None:
        self.states: set[str] = set()
        self.actions: set[str] = set()
        self.assets: set[str] = set()

    def read(self, path: str) -> None:
        vocabulary = load_vocabulary()
        parts = path.split(".")
        if parts[0] == "deps" and len(parts) > 1 and parts[1] == SELF_KEY:
            parts = parts[1:]
        if parts[0] != SELF_KEY or len(parts) < 2:
            return
        head = parts[1]
        if head == vocabulary.tasks_segment:
            if len(parts) > 2:
                self.actions.add(parts[2])
        elif head == vocabulary.assets_segment:
            if len(parts) > 2:
                self.assets.add(parts[2])
        elif head not in vocabulary.agent_fields:
            self.states.add(head)

    def visit_component(self, node: ComponentNodeInput, ancestors: tuple[ComponentNodeInput, ...], scope: Mapping[str, None]) -> None:
        return None

    def visit_path(self, path: str, where: Where, scope: Mapping[str, None]) -> None:
        if path.split(".")[0] not in scope:
            self.read(path)

    def visit_agent_call(self, call: AgentProbeInput, where: Where, scope: Mapping[str, None], into: str | None) -> None:
        if call.dependency == SELF_KEY:
            self.actions.add(action_key_for(call))

    def visit_util_call(self, call: UtilCallInput, where: Where, scope: Mapping[str, None], top: bool) -> None:
        return None

    def bind_let(self, name: str, items: ComponentPropInput | None, where: Where, scope: Mapping[str, None]) -> None:
        return None

    def bind_into(self, name: str, call: AgentProbeInput) -> None:
        return None


def self_reads(
    tree: ComponentNodeInput,
    queries: Sequence[BlokQueryInput] = (),
    subscriptions: Sequence[BlokSubscriptionInput] = (),
) -> _SelfReads:
    """What ``tree`` and the variables of its queries read of ``self``."""
    reads = _SelfReads()
    walk_tree(tree, reads)
    for declared in (*queries, *subscriptions):
        for path in argument_paths(declared.variables):
            reads.read(path)
    return reads


def self_dependency(reads: _SelfReads, app_registry: "AppRegistry") -> AgentDependencyInput | None:
    """The ``self`` dependency a blok's reads demand of this app, or None when it reads none of it.

    Raises:
        BlokError: If it calls or watches an action, reads a state, or names an asset this app
            does not declare.
    """
    vocabulary = load_vocabulary()
    for name in sorted(reads.assets):
        if name not in app_registry.registered_assets:
            raise BlokError("deps.asset", path=f"self.assets.{name} (registered: {', '.join(sorted(app_registry.registered_assets)) or 'none'})")
    clashing = sorted(set(app_registry.states) & vocabulary.reserved_state_names)
    if clashing and reads.states:
        raise BlokError("deps.reserved_state", name=clashing[0])
    actions: list[ActionDependencyInput] = []
    for action in sorted(reads.actions):
        implementation = app_registry.implementations.get(action)
        if implementation is None:
            raise BlokError("deps.unknown_action", path=f"self.{action}", key=SELF_KEY, action=action, actions=", ".join(sorted(app_registry.implementations)) or "none")
        actions.append(build_action_dependency_input(key=action, definition=implementation.definition, action_key=action))
    states: list[StateDependencyInput] = []
    for state in sorted(reads.states):
        implementation_state = app_registry.states.get(state)
        if implementation_state is None:
            raise BlokError("deps.unknown_state", path=f"self.{state}", key=SELF_KEY, state=state, states=", ".join(sorted(app_registry.states)) or "none")
        states.append(build_state_dependency_input(key=state, state_key=state, definition=implementation_state.definition))
    if not actions and not states and not reads.assets:
        return None
    return AgentDependencyInput(
        key=SELF_KEY,
        app=None,
        optional=False,
        auto_resolvable=False,
        action_dependencies=tuple(actions) or None,
        state_dependencies=tuple(states) or None,
    )


def check_unknown_module(module: str | None, app_registry: "AppRegistry") -> list[Finding]:
    """``module.unknown`` when ``module`` is neither declared by this app nor the Orkestrator's:
    its components are then unknowable, so nothing of them is checked."""
    if module is None or module == ORKESTRATOR_MODULE or module in app_registry.declared_catalogs:
        return []
    declared = ", ".join(sorted({ORKESTRATOR_MODULE, *app_registry.declared_catalogs}))
    return [Finding.of("module.unknown", f"module {module}", module=module, modules=declared)]


def self_arg_ports(app_registry: "AppRegistry") -> ArgPorts:
    """The full arg ports of this app's actions, as ``self`` calls them: what a local passed to
    one whole takes its port from."""
    return {SELF_KEY: {interface: implementation.definition.args or () for interface, implementation in app_registry.implementations.items()}}


def _report(owner: str, finding: Finding, mode: BlokCheckMode, at: DeclaredAt | None = None, reported: frozenset[str] = frozenset()) -> None:
    """Raise an error (any finding, in strict mode); warn the rest once, at the declaring line."""
    if finding.level == "error" or mode == "strict":
        where = f" ({finding.path})" if finding.path else ""
        raise BlokError.from_finding(dataclass_replace(finding, path=None, message=f"{owner}{where}: {finding.message}"))
    if finding_key(finding) not in reported:
        warn_at(f"{owner}: {finding.message} [{finding.code}]", at)


def type_queries(
    owner: str,
    queries: Sequence[BlokQueryInput],
    subscriptions: Sequence[BlokSubscriptionInput],
    app_registry: "AppRegistry",
    mode: BlokCheckMode,
    at: DeclaredAt | None = None,
) -> dict[str, PortMatchInput]:
    """Check each declared query and subscription against its service's schema, where this app
    knows it, and return each query's value as the schema answers it.

    A service the app knows no schema for, or graphql-core missing, leaves it unchecked: a
    warning, refused in strict mode. A mutation, or a document that is not its kind, is refused.
    """
    values: dict[str, PortMatchInput] = {}
    checkable = graphql_available()
    for kind, declared in (*(("query", q) for q in queries), *(("subscription", s) for s in subscriptions)):
        service = app_registry.services.get(declared.service)
        sdl = service.get_graphql_schema() if service is not None else None
        if sdl is None:
            _report(owner, Finding.of("query.schema", f"{kind} {declared.key}", kind=kind, name=declared.key, problem=f"this app knows no schema for the {declared.service!r} service, so it is not checked; register the service with its schema to check it"), mode, at)
            continue
        if not checkable:
            _report(owner, Finding.of("query.schema", f"{kind} {declared.key}", kind=kind, name=declared.key, problem="install graphql-core to check it"), mode, at)
            continue
        shape = check_query(sdl, declared.document, kind)
        for error in shape.errors:
            code = "query.read_only" if "mutation" in error or "operation" in error else "query.schema"
            _report(owner, Finding.of(code, f"{kind} {declared.key}", kind=kind, name=declared.key, problem=error), mode, at)
        names = {variable.name for variable in shape.variables}
        for variable in declared.variables:
            if variable.key is not None and variable.key not in names:
                _report(owner, Finding.of("query.variable", f"{kind} {declared.key}", kind=kind, name=declared.key, variable=variable.key, problem="is not a variable of its document"), mode, at)
        if isinstance(declared, BlokQueryInput):
            given = {variable.key for variable in declared.variables}
            for variable in shape.variables:
                if variable.required and variable.name not in given:
                    _report(owner, Finding.of("query.variable", f"{kind} {declared.key}", kind=kind, name=declared.key, variable=variable.name, problem="is required and the blok sets nothing for it"), mode, at)
            if shape.data is not None:
                values[declared.key] = shape.data.model_copy(update={"key": "value"})
                if declared.more is not None:
                    problem = _more_problem(shape.data, declared.more)
                    if problem is not None:
                        _report(owner, Finding.of("query.more", f"{kind} {declared.key}", name=declared.key, more=declared.more, problem=problem), mode, at)
    return values


def _more_problem(data: PortMatchInput, more: str) -> str | None:
    """Why ``more`` is no list field of a query's data, or None when it is one."""
    current = data
    for part in more.split("."):
        child = next((candidate for candidate in current.children or () if candidate.key == part), None)
        if child is None:
            return f"is no field of its result ({part!r} is not in {current.key or 'its data'})"
        current = child
    if current.kind != PortKind.LIST:
        return "is no list field of its result, so new pages have nothing to append to"
    return None


def catalogs_of(tree: ComponentNodeInput, view: BlokView) -> tuple[str, ...]:
    """The standard catalogs (``ui@1``) whose components the tree uses; base is every renderer's."""
    found: set[str] = set()
    for node in iter_nodes(tree):
        spec = view.component(node.component)
        if spec is not None and spec.catalog is not None and "@" in spec.catalog and not spec.catalog.startswith(f"{BASE_NAME}@"):
            found.add(spec.catalog)
    return tuple(sorted(found))


def build_declared_bloks(app_registry: "AppRegistry") -> dict[str, BlokImplementationInput]:
    """Generate the blok inputs of every declaration against ``app_registry``.

    Raises:
        BlokError: If a blok reads what the app does not declare, or breaks a rule of the
            language (see :mod:`.validate`).
    """
    declared: dict[str, BlokImplementationInput] = {}
    for key, declaration in app_registry.registered_bloks.items():
        owner = f"Blok {key!r}"
        context = ComponentContext(declaration.context).value
        kind = "action_logo" if context == ComponentContext.ACTION_LOGO.value else "agent"
        dependency = None
        if kind == "agent":
            dependency = self_dependency(self_reads(declaration.component, declaration.queries, declaration.subscriptions), app_registry)
        dependencies = (dependency,) if dependency is not None else ()
        query_values = type_queries(owner, declaration.queries, declaration.subscriptions, app_registry, declaration.mode, declaration.declared_at)
        view = app_registry.blok_view(declaration.module)
        demo = declaration.demo if declaration.demo is not None else _demo(dependency, app_registry)
        catalogs = declaration.catalogs or catalogs_of(declaration.component, view)
        scope = BlokScope(
            kind=kind,
            context=context,
            dependencies=dependencies,
            locals=declaration.locals,
            queries=declaration.queries,
            subscriptions=declaration.subscriptions,
            args=tuple(port.key for port in declaration.args),
            catalogs=catalogs,
            demo=demo,
            query_values=query_values,
            assets=frozenset(app_registry.registered_assets),
        )
        scope = resolve_scope(declaration.component, scope, view, arg_ports=self_arg_ports(app_registry))
        # What the declaration already told (registering it) is not told again.
        for finding in check_blok_tree(declaration.component, scope, view):
            _report(owner, finding, declaration.mode, declaration.declared_at, declaration.reported)
        # Empty declarations stay unset, so a dump with ``exclude_unset`` (what an agent sends)
        # carries only what the blok declares; ``vocab`` is always there.
        fields: dict[str, object] = {
            "key": key,
            "vocab": load_vocabulary().vocab,
            "dependencies": dependencies,
            "context": declaration.context,
        }
        optional: dict[str, object] = {
            "description": declaration.description,
            "module": declaration.module,
            "default": declaration.default or None,
            "for_model": declaration.for_model,
            "for_node": declaration.for_node,
            "args": declaration.args or None,
            "catalogs": catalogs or None,
            "locals": declaration.locals or None,
            "demo": demo,
            "queries": declaration.queries or None,
            "subscriptions": declaration.subscriptions or None,
        }
        fields.update({name: value for name, value in optional.items() if value is not None})
        checked = check_blok(BlokImplementationInput.model_validate({**fields, "components": (declaration.component,)}))
        declared[key] = (
            BlokImplementationInput.model_validate({**fields, "source": declaration.source})
            if declaration.source is not None
            else checked
        )
    return declared


def resolved_locals(blok: BlokImplementationInput, app_registry: "AppRegistry") -> tuple[BlokLocalInput, ...]:
    """The locals of a built blok as its tree resolves them, with this app's full arg ports: what
    a server stores as the blok's resolved locals. The input itself carries them as written."""
    from arkitekt_spec.blok.bsx import parse_bsx

    view = app_registry.blok_view(blok.module)
    trees = blok.components or ((parse_bsx(blok.source, view=view),) if blok.source is not None else ())
    scope = BlokScope(locals=tuple(blok.locals or ()), dependencies=tuple(blok.dependencies))
    if not trees:
        return tuple(resolve_local(local, (), ()) for local in scope.locals)
    return resolve_scope(trees[0], scope, view, arg_ports=self_arg_ports(app_registry)).locals


_DEMO_SCALARS: dict[PortKind, JSON] = {
    PortKind.STRING: "",
    PortKind.INT: 0,
    PortKind.FLOAT: 0.0,
    PortKind.QUANTITY: 0.0,
    PortKind.BOOL: False,
    PortKind.LIST: [],
    PortKind.DICT: {},
}


def _demo(dependency: AgentDependencyInput | None, app_registry: "AppRegistry") -> BlokDemoInput | None:
    """A placeholder for every state of ``self`` the blok reads, from the states' ports."""
    if dependency is None or not dependency.state_dependencies:
        return None
    states: dict[str, JSON] = {}
    for demand in dependency.state_dependencies:
        state = app_registry.states.get(demand.key)
        if state is None:
            return None
        states[demand.key] = {port.key: _demo_value(port) for port in state.definition.ports or ()}
    return BlokDemoInput(deps={SELF_KEY: states})


def _demo_value(port: ReturnPortInput) -> JSON:
    if port.nullable:
        return None
    if port.kind == PortKind.ENUM:
        first = next(iter(port.choices or ()), None)
        value = first.value if first is not None else ""
        return value if isinstance(value, (str, int, float, bool)) else ""
    if port.kind in (PortKind.MODEL, PortKind.INTERFACE):
        return {child.key: _demo_value(child) for child in port.children or ()}
    return _DEMO_SCALARS.get(PortKind(port.kind), None)


__all__ = ["SELF_KEY", "build_declared_bloks", "catalogs_of", "check_unknown_module", "resolved_locals", "self_arg_ports", "self_dependency", "self_reads", "type_queries"]
