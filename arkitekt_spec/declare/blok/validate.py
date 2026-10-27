"""Validate a v2 blok: its tree against what it declares, its kind, and the catalogs that draw it.

Every rule is a row of the vocabulary's diagnostic table and is driven by its data: the one
reserved list, the per-kind root matrix, the record shapes, the function signatures and the
theme. A finding is an error (the blok is refused) or a warning (it is reported).

- **Roots.** A path starts at a root its kind reads (``self``, ``deps``, ``args``, ``task``,
  ``queries``, ``subscriptions``, ``viewer``, ``then``), at a declared local, a ``let`` or an
  ``into`` name; ``event`` only in a callback, ``error`` only in an ``<error>`` slot.
- **Agents.** ``self`` / ``deps.<key>``: ``.<state>.<field>``, ``.tasks.<action>`` (a task
  record), ``.assets.<name>``, ``.id``/``.name``; calls ``.<action>(...)``.
- **Callbacks.** A callback prop holds one task call or one effect (``ui.copy``,
  ``task.answer``, ``query.refetch``, ``query.fetch_more``); nothing else holds them. A query
  effect names a declared query by a literal, and ``fetch_more`` one that declares ``more``.
- **Locals.** A local is a port: a control writes it only if the catalog kind of its ``bind``
  prop takes the local's kind (``bind_kinds``), a task call passes it whole only to an arg port
  of a kind that takes it (``arg_kinds``), where the action is known. A local whose kind was left
  out takes it from the tree first (:mod:`.infer`); an ``ANY`` local is checked against nothing.
- **Dependencies.** A custom blok's undeclared ``deps.<key>`` are inferred (:mod:`.infer`).
- **Loops.** A ``foreach`` without ``key`` over items known not to be scalars is a warning.
- **Returns.** Reading ``<record>.returns.return<N>`` (an unnamed return) is a warning.
- **Shadowing.** Using a component a module redefines over a standard catalog is a warning,
  once per name.
- **Records.** Two call sites never share an ``into``.
- **Literals.** A string literal on a ``BOOL``/``INT``/``FLOAT`` prop is refused.
- **Theme.** A raw palette colour in a class is a warning naming the role; a logo paints with
  the pens only; a scene's ``color`` is a colour role (a warning otherwise).
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from arkitekt_spec.actions import (
    LOCAL_ANY,
    ActionArgumentInput,
    AgentDependencyInput,
    AgentProbeInput,
    ArgPortInput,
    BlokDemoInput,
    BlokLocalInput,
    BlokQueryInput,
    BlokSubscriptionInput,
    ComponentContext,
    ComponentNodeInput,
    ComponentPropInput,
    PortKind,
    PortMatchInput,
    StateDependencyInput,
    StateImplementationInput,
    UtilCallInput,
)
from arkitekt_spec.blok.bsx import FETCH_MORE_FUNCTION, QUERY_SUGAR
from arkitekt_spec.blok.catalogs import BlokView, default_view
from arkitekt_spec.blok.vocabulary import (
    JSON,
    BlokError,
    Finding,
    RecordField,
    Vocabulary,
    load_vocabulary,
)
from arkitekt_spec.declare.blok.walk import (
    BIND_PROP,
    FOREACH_ITEMS_PROP,
    FOREACH_KEY_PROP,
    WHEN_PROP,
    Where,
    action_key_for,
    argument_paths,
    foreach_let,
    into_sites,
    is_callback_value,
    iter_nodes,
    walk_tree,
)
from arkitekt_spec.declare.catalogs import ASSET_KIND, CALLBACK_KIND, BlokCheckMode, ComponentSpec
from arkitekt_spec.declare.definition.match import build_port_match, build_port_matches

OPEN_IDENTIFIER = "*"
"""Marks a value nothing declares the shape of: any path below it is taken as written."""
ITEM = "..."
TOAST_COMPONENT = "Toast"
TOAST_TASK_PROP = "task"
CLASS_NAME = "className"
ACTION_LOGO = "ACTION_LOGO"
LOGO = "LOGO"

Match = PortMatchInput | None


def open_match(key: str | None) -> PortMatchInput:
    """A value of unknown shape that may still be looked into."""
    return PortMatchInput(key=key, identifier=OPEN_IDENTIFIER)


def is_open(match: Match) -> bool:
    """Whether ``match`` takes any path below it as written."""
    return match is not None and match.identifier == OPEN_IDENTIFIER


_KINDS = {kind.value: kind for kind in PortKind}
_VERBS = {function: verb for verb, function in QUERY_SUGAR.items()}
_COMPOUND_KINDS = frozenset({"DICT", "MODEL", "LIST", "UNION"})
"""Item kinds that are no scalar: a foreach over them is keyed by a stable field, not its position."""
_OPAQUE_KINDS = frozenset({"STRUCTURE", "MEMORY_STRUCTURE", "INTERFACE", "UNION"})
"""Port kinds whose fields the port does not declare: a path below such a local is taken as written."""


def kind_name(kind: object) -> str:
    """A port kind as the vocabulary writes it (``INT``), whether an enum member or its value."""
    return kind.value if isinstance(kind, PortKind) else str(kind)


def local_arg_port(local: BlokLocalInput) -> ArgPortInput | None:
    """A local as the arg port it is, or None for an ``ANY`` local (no port is ``ANY``)."""
    if kind_name(local.kind) == LOCAL_ANY:
        return None
    return ArgPortInput.model_validate(local.model_dump(exclude={"persist"}))


def record_match(key: str, fields: Iterable[RecordField], overrides: Mapping[str, PortMatchInput] | None = None) -> PortMatchInput:
    """A record of the vocabulary as a match; ``overrides`` replace fields by key (a typed ``value``)."""
    children: list[PortMatchInput] = []
    for record_field in fields:
        override = (overrides or {}).get(record_field.key)
        if override is not None:
            children.append(override.model_copy(update={"key": record_field.key}))
            continue
        if record_field.children:
            children.append(record_match(record_field.key, record_field.children).model_copy(update={"nullable": record_field.nullable or None}))
        elif record_field.kind in ("DICT", "ANY", "LIST"):
            children.append(open_match(record_field.key))
        else:
            children.append(PortMatchInput(key=record_field.key, kind=_KINDS.get(record_field.kind), nullable=record_field.nullable or None))
    return PortMatchInput(key=key, kind=PortKind.DICT, children=tuple(children))


def task_record_match(key: str, returns: Sequence[PortMatchInput] | None = None) -> PortMatchInput:
    """The task record (``task``, ``.tasks.<action>``, an ``into`` name), ``returns`` typed when known."""
    record = load_vocabulary().records["task"]
    overrides = {"returns": PortMatchInput(key="returns", kind=PortKind.DICT, children=tuple(returns))} if returns else None
    return record_match(key, record.fields, overrides)


def blok_context(tree: ComponentNodeInput) -> ComponentContext:
    """The context a blok's root names (``<Html>`` -> HTML, ``<Overlay>`` -> OVERLAY, ...).

    Raises:
        BlokError: ``context.root_unknown`` if the root names no context.
    """
    roots = load_vocabulary().blok_roots
    if tree.component not in roots:
        raise BlokError.from_finding(
            Finding.of("context.root_unknown", tree.id, roots=", ".join(f"<{root}>" for root in roots), component=tree.component)
        )
    return ComponentContext(roots[tree.component])


# --------------------------------------------------------------------------- the scope


@dataclass(frozen=True)
class BlokScope:
    """What a blok declares beside its tree: what its reads and calls resolve against.

    Args:
        kind: ``agent``, ``custom``, ``task``, ``action`` or ``action_logo`` (the root matrix).
        context: The blok's context (``HTML``, ``SCENE``, ``OVERLAY``, ``LOGO``, ``ACTION_LOGO``).
        dependencies: What ``self`` and ``deps.<key>`` resolve against; a ``self`` entry types
            ``self``, which is open without one.
        args: The keys ``args.<key>`` may read; ``None`` reads any.
        catalogs: The catalogs it declares (``ui@1``); a component of a standard catalog it does
            not declare is a warning. None declared: not checked.
        query_values: Per declared query, the shape of its value as its service answers it.
        assets: The assets ``self.assets.<name>`` may name; ``None`` names any.
    """

    kind: str = "agent"
    context: str = "HTML"
    dependencies: tuple[AgentDependencyInput, ...] = ()
    locals: tuple[BlokLocalInput, ...] = ()
    queries: tuple[BlokQueryInput, ...] = ()
    subscriptions: tuple[BlokSubscriptionInput, ...] = ()
    args: tuple[str, ...] | None = None
    catalogs: tuple[str, ...] = ()
    demo: BlokDemoInput | None = None
    query_values: Mapping[str, PortMatchInput] = field(default_factory=dict[str, PortMatchInput])
    assets: frozenset[str] | None = None

    @property
    def drawn_in(self) -> str:
        """The context its components are drawn in: an action logo's is a logo's."""
        return LOGO if self.context == ACTION_LOGO else self.context


# --------------------------------------------------------------------------- the checker


def _colour_pattern(vocabulary: Vocabulary) -> re.Pattern[str]:
    utilities = "|".join(sorted((re.escape(u) for u in vocabulary.theme.colour_utilities), key=len, reverse=True))
    colours = "|".join(vocabulary.theme.palette_colours)
    return re.compile(rf"^(?P<utility>{utilities})-(?P<colour>{colours})(?:-(?P<shade>\d{{2,3}}))?(?:/\d+)?$")


_ARBITRARY_COLOUR = re.compile(r"^(?P<utility>bg|text|border|ring|fill|stroke|from|via|to|outline|decoration|shadow)-\[(#|rgb|hsl|oklch|color)")
RETURNS_FIELD = "returns"
_UNNAMED_RETURN = re.compile(r"return[0-9]+")
"""The key an action's return gets when it is not named (``return0``)."""
LITERAL_KINDS = ("BOOL", "INT", "FLOAT")
"""Prop kinds a string literal never fits: ``fullWidth="true"`` is the string, write ``fullWidth={true}``."""
SCENE = "SCENE"


class _Checker:
    """Walks a tree with a :class:`BlokScope` and collects findings."""

    def __init__(self, scope: BlokScope, view: BlokView | None) -> None:
        self.scope = scope
        self.view = view
        self.vocabulary = load_vocabulary()
        self.kind = self.vocabulary.kind(scope.kind)
        self.findings: list[Finding] = []
        self.dependencies = {dependency.key: dependency for dependency in scope.dependencies}
        self.locals = {local.key: local for local in scope.locals}
        self.queries = {query.key: query for query in scope.queries}
        self.subscriptions = {subscription.key: subscription for subscription in scope.subscriptions}
        self.bound_locals: set[str] = set()
        self.shadowed: set[str] = set()
        self.into: set[str] = set()
        self.colour = _colour_pattern(self.vocabulary)

    def add(self, code: str, at: str | None, /, **fields: object) -> None:
        self.findings.append(Finding.of(code, at, **fields))

    # -- declarations

    def check_declarations(self, tree: ComponentNodeInput) -> None:
        vocabulary = self.vocabulary
        scope = self.scope
        wrong_logo = (scope.context == ACTION_LOGO) != (scope.kind == "action_logo")
        if wrong_logo or scope.drawn_in not in self.kind.contexts:
            self.add("context.kind", None, n=_article(scope.kind), kind=scope.kind, contexts=", ".join(self.kind.contexts), context=scope.context)
        roots = {context: root for root, context in vocabulary.blok_roots.items()}
        if scope.context in roots and tree.component != roots[scope.context]:
            self.add("context.root", tree.id, context=scope.context.lower(), root=roots[scope.context], component=tree.component)
        if scope.locals and not self.kind.locals:
            self.add("roots.not_in_kind", None, path="locals", n=_article(scope.kind), kind=scope.kind, root="locals", roots=", ".join(sorted(self.kind.roots)))
        for local in scope.locals:
            if local.key in vocabulary.reserved:
                self.add("roots.reserved", f"locals.{local.key}", name=local.key)
            self.check_local_default(local)
        for name, sites in into_sites(tree).items():
            self.into.add(name)
            if name in vocabulary.reserved:
                self.add("roots.reserved", sites[0][1], name=name)
            if name in self.locals:
                self.add("roots.shadow", sites[0][1], name=name, first="a local", second=f"the record of {sites[0][1]}")
            if len(sites) > 1:
                self.add("into.twice", sites[1][1], name=name, first=sites[0][1], second=sites[1][1])
        for node in iter_nodes(tree):
            let = foreach_let(node)
            if let is None:
                continue
            if let in vocabulary.reserved:
                self.add("roots.reserved", node.id, name=let)
            elif let in self.locals or let in self.into:
                self.add("roots.shadow", node.id, name=let, first="a local" if let in self.locals else "a call's record", second=f"the let of <foreach> ({node.id})")
        for query in scope.queries:
            for path in argument_paths(query.variables):
                self.resolve(path, None, {}, owner=f"query {query.key}")
        for subscription in scope.subscriptions:
            for path in argument_paths(subscription.variables):
                self.resolve(path, None, {}, owner=f"subscription {subscription.key}")
            if subscription.history is not None and subscription.history not in self.queries:
                self.add("query.history", f"subscriptions.{subscription.key}", name=subscription.key, history=subscription.history)

    def check_local_default(self, local: BlokLocalInput) -> None:
        """A local's default fits its port: a number for a number, a list for a list, one of its choices."""
        kind = kind_name(local.kind)
        if kind == LOCAL_ANY:
            return
        kinds: dict[str, type | tuple[type, ...]] = {
            "STRING": str, "DATE": str, "STRUCTURE": str, "INT": int, "FLOAT": (int, float), "QUANTITY": (int, float),
            "BOOL": bool, "LIST": list, "DICT": dict, "MODEL": dict,
        }
        value: object = local.default
        if value is None:
            return
        expected = kinds.get(kind)
        wrong = expected is not None and (not isinstance(value, expected) or (kind in ("INT", "FLOAT", "QUANTITY") and isinstance(value, bool)))
        if kind == "ENUM" and local.choices is not None:
            wrong = value not in [choice.value for choice in local.choices]
        if wrong:
            self.add("local.default", f"locals.{local.key}", name=local.key, default=value, kind=kind)

    def local_port(self, path: str) -> ArgPortInput | None:
        """The port a path into a local names (the local, or a field of it), or None when its
        shape does not say (an ``ANY`` local)."""
        parts = path.split(".")
        local = self.locals.get(parts[0])
        port: ArgPortInput | None = local_arg_port(local) if local is not None else None
        for part in parts[1:]:
            if port is None:
                return None
            children = port.children or ()
            child = next((candidate for candidate in children if candidate.key == part), None)
            if child is None and len(children) == 1 and children[0].key == ITEM:
                child = children[0]
            port = child
        return port

    def finish(self) -> None:
        for local in self.scope.locals:
            if local.persist and local.key not in self.bound_locals:
                self.add("local.unused_persist", f"locals.{local.key}", name=local.key)

    # -- the visitor

    def visit_component(self, node: ComponentNodeInput, ancestors: tuple[ComponentNodeInput, ...], scope: Mapping[str, Match]) -> None:
        spec = self.view.component(node.component) if self.view is not None else None
        if self.view is not None and spec is None and self.view.complete:
            self.add("catalog.unknown_component", node.id, component=node.component, catalogs=self.view.label)
        if spec is not None:
            self.check_catalog(node, spec, ancestors)
        shadow = self.view.shadows.get(node.component) if self.view is not None else None
        if shadow is not None and node.component not in self.shadowed:
            self.shadowed.add(node.component)
            self.add("catalog.shadow", node.id, component=node.component, catalog=shadow.catalog, module=shadow.module, winner=shadow.winner)
        for prop in node.props or ():
            self.check_prop(node, prop, spec)

    def check_catalog(self, node: ComponentNodeInput, spec: ComponentSpec, ancestors: tuple[ComponentNodeInput, ...]) -> None:
        assert self.view is not None
        if node.children and not spec.accepts_children:
            self.add("catalog.children", node.id, component=node.component)
        present = {prop.key for prop in node.props or ()}
        known = {prop.key for prop in spec.props} | {WHEN_PROP}
        unknown = sorted(present - known)
        if unknown:
            self.add("catalog.props", node.id, component=node.component, detail=f"has no props {unknown}")
        missing = sorted(prop.key for prop in spec.props if prop.required and prop.key not in present)
        if missing:
            self.add("catalog.props", node.id, component=node.component, detail=f"requires props {missing}")
        context = "HTML"
        for ancestor in ancestors:
            above = self.view.component(ancestor.component)
            if above is not None and not above.inherits_context:
                context = above.children_context or above.context
        if not spec.inherits_context and spec.context != context:
            self.add("catalog.placement", node.id, component=node.component, detail=f"is a {spec.context.lower()} component, not one of the {context.lower()} context")
        names = {ancestor.component for ancestor in ancestors}
        if spec.downstream_of and not set(spec.downstream_of) & names:
            self.add("catalog.placement", node.id, component=node.component, detail=f"must be inside one of {sorted(spec.downstream_of)}")
        forbidden = sorted(set(spec.not_downstream_of) & names)
        if forbidden:
            self.add("catalog.placement", node.id, component=node.component, detail=f"must not be inside {forbidden}")
        # Only the versioned standard catalogs are declared; a module (the Orkestrator's, an
        # app's) is what the blok's ``module`` names.
        catalog = spec.catalog
        standard = catalog is not None and catalog.split("@", 1)[0] in self.vocabulary.catalogs and "@" in catalog
        if self.scope.catalogs and standard and catalog not in self.scope.catalogs:
            self.add("catalog.undeclared", node.id, component=node.component, catalog=catalog)

    def check_prop(self, node: ComponentNodeInput, prop: ComponentPropInput, spec: ComponentSpec | None) -> None:
        vocabulary = self.vocabulary
        prop_spec = spec.prop(prop.key) if spec is not None else None
        holds = is_callback_value(prop)
        has_value = any(
            value is not None for value in (prop.static_value, prop.dynamic_value, prop.util_call, prop.agent_call)
        )
        if prop_spec is not None:
            if prop_spec.kind == CALLBACK_KIND and has_value and not holds:
                self.add("callback.unbound", node.id, key=prop.key, component=node.component)
            elif prop_spec.kind != CALLBACK_KIND and holds:
                self.add("callback.not_callback", node.id, key=prop.key, component=node.component, what=_what(prop))
        if prop_spec is not None and prop_spec.kind in LITERAL_KINDS and isinstance(prop.static_value, str) and prop.key != BIND_PROP:
            self.add("props.literal_type", node.id, component=node.component, key=prop.key, value=prop.static_value, kind=prop_spec.kind, hint=literal_hint(prop.key, prop.static_value, prop_spec.kind))
        if prop_spec is not None and prop_spec.values is not None and isinstance(prop.static_value, str) and prop.static_value not in prop_spec.values:
            self.add("catalog.value", node.id, component=node.component, key=prop.key, value=prop.static_value, values=", ".join(prop_spec.values))
        if prop_spec is not None and prop_spec.kind == ASSET_KIND:
            path = prop.dynamic_value.path if prop.dynamic_value is not None else None
            parts = (path or "").split(".")
            agent = parts[1:] if parts[0] == "self" else parts[2:] if parts[0] == "deps" else []
            if len(agent) != 2 or agent[0] != vocabulary.assets_segment:
                self.add("catalog.asset", node.id, key=prop.key, component=node.component)
        if prop.util_call is not None and prop.util_call.operation == "task.answer" and "task" not in self.kind.roots:
            self.add("roots.not_in_kind", node.id, path="task.answer", n=_article(self.scope.kind), kind=self.scope.kind, root="task", roots=", ".join(sorted(self.kind.roots)))
        if prop.key == BIND_PROP:
            path = prop.dynamic_value.path if prop.dynamic_value is not None else None
            if path is None:
                self.add("local.bind", node.id, what=_what(prop))
            else:
                root = path.split(".")[0]
                if root not in self.locals:
                    self.add("local.unknown", node.id, path=path, root=root)
                else:
                    self.bound_locals.add(root)
                    if prop_spec is not None:
                        self.check_bind_kind(node, prop_spec.kind, path)
        if node.component == TOAST_COMPONENT and prop.key == TOAST_TASK_PROP:
            path = prop.dynamic_value.path if prop.dynamic_value is not None else None
            if path is None or (path != "task" and path not in self.into):
                self.add("toast.task", node.id, what=path or _what(prop))
        if isinstance(prop.static_value, str):
            if prop.key == CLASS_NAME:
                self.check_classes(prop.static_value, node.id)
            if self.scope.drawn_in == LOGO and prop.key in vocabulary.theme.logo_paint_props:
                self.check_paint(node, prop.key, prop.static_value)
            if spec is not None and spec.context == SCENE and prop.key in vocabulary.theme.scene_paint_props:
                self.check_scene_colour(node, prop.key, prop.static_value)
        if prop.key == CLASS_NAME and prop.util_call is not None:
            for literal in _class_literals(prop.util_call):
                self.check_classes(literal, node.id)

    def check_bind_kind(self, node: ComponentNodeInput, prop_kind: str, path: str) -> None:
        """The local a control writes is of a kind the control writes (``Slider``: FLOAT or INT)."""
        allowed = self.vocabulary.bind_kinds.get(prop_kind)
        port = self.local_port(path)
        if allowed is None or port is None:
            return
        kind = kind_name(port.kind)
        if kind not in allowed:
            self.add("local.bind_kind", node.id, key=BIND_PROP, component=node.component, prop_kind=prop_kind, path=path, kind=kind, kinds=", ".join(sorted(allowed)))

    def check_classes(self, classes: str, owner: str) -> None:
        hints = self.vocabulary.theme.palette_role_hints
        for token in classes.split():
            base = token.rsplit(":", 1)[-1].lstrip("!")
            match = self.colour.match(base)
            if match is not None:
                role = hints.get(match.group("colour"), "primary")
                self.add("palette.raw_colour", owner, token=token, suggestion=f"{match.group('utility')}-{role}")
                continue
            arbitrary = _ARBITRARY_COLOUR.match(base)
            if arbitrary is not None:
                self.add("palette.raw_colour", owner, token=token, suggestion=f"{arbitrary.group('utility')}-<role> ({', '.join(self.vocabulary.theme.colour_roles[:6])}, ...)")

    def check_paint(self, node: ComponentNodeInput, key: str, value: str) -> None:
        paint = value.strip()
        if paint in self.vocabulary.theme.logo_pens or (paint.startswith("url(#") and paint.endswith(")")):
            return
        self.add("palette.logo_paint", node.id, component=node.component, prop=key, value=value, pens="|".join(self.vocabulary.theme.logo_pens))

    def check_scene_colour(self, node: ComponentNodeInput, key: str, value: str) -> None:
        """A scene paints with the theme's colour roles, resolved by the renderer against the viewer's brand."""
        roles = self.vocabulary.theme.colour_roles
        if value.strip() in roles:
            return
        self.add("palette.scene_colour", node.id, component=node.component, prop=key, value=value, roles=", ".join(roles))

    def visit_path(self, path: str, where: Where, scope: Mapping[str, Match]) -> None:
        self.resolve(path, where, scope, owner=where.text)

    def visit_agent_call(self, call: AgentProbeInput, where: Where, scope: Mapping[str, Match], into: str | None) -> None:
        kind = self.kind
        owner = where.text
        action = action_key_for(call)
        if call.dependency == "self":
            if "self" not in kind.roots:
                self.add("roots.not_in_kind", where.node.id, path=f"self.{action}(...)", n=_article(self.scope.kind), kind=self.scope.kind, root="self", roots=", ".join(sorted(kind.roots)))
                return
        elif "deps" not in kind.roots:
            self.add("roots.not_in_kind", where.node.id, path=f"deps.{call.dependency}.{action}(...)", n=_article(self.scope.kind), kind=self.scope.kind, root="deps", roots=", ".join(sorted(kind.roots)))
            return
        dependency = self.dependencies.get(call.dependency)
        if dependency is None:
            if call.dependency != "self":
                self.add("deps.unknown", where.node.id, path=owner, key=call.dependency, keys=", ".join(sorted(self.dependencies)) or "none", hint=dependency_hint(call.dependency, self.scope.kind))
            return
        actions = dependency.action_dependencies
        if actions is not None and action not in {demand.key for demand in actions}:
            self.add("deps.unknown_action", where.node.id, path=owner, key=call.dependency, action=action, actions=", ".join(sorted(demand.key for demand in actions)) or "none")
            return
        demand = next((each.demand for each in actions or () if each.key == action), None)
        if demand is not None and demand.arg_matches:
            self.check_arg_kinds(call, demand.arg_matches, where, scope)

    def check_arg_kinds(self, call: AgentProbeInput, matches: Sequence[PortMatchInput], where: Where, scope: Mapping[str, Match]) -> None:
        """A local passed whole to a task call fits the action's arg port (``arg_kinds``)."""
        ports = {match.key: match for match in matches if match.key is not None}
        name = f"self.{call.operation}" if call.dependency == "self" else f"deps.{call.dependency}.{call.operation}"
        for argument in call.arguments or ():
            path = argument.value_path
            match = ports.get(argument.key or "")
            if path is None or match is None or match.kind is None or path.split(".")[0] in scope:
                continue
            port = self.local_port(path) if path.split(".")[0] in self.locals else None
            if port is None:
                continue
            kind = kind_name(port.kind)
            port_kind = kind_name(match.kind)
            allowed = self.vocabulary.arg_kinds.get(kind)
            identifiers = (port.identifier, match.identifier)
            other_structure = kind == "STRUCTURE" and port_kind == "STRUCTURE" and None not in identifiers and identifiers[0] != identifiers[1]
            if (allowed is not None and port_kind not in allowed) or other_structure:
                shown = f"{port_kind} {match.identifier}" if other_structure else port_kind
                self.add("local.arg_kind", where.node.id, call=name, argument=argument.key, port_kind=shown, path=path, kind=f"{kind} {port.identifier}" if other_structure else kind)

    def visit_util_call(self, call: UtilCallInput, where: Where, scope: Mapping[str, Match], top: bool) -> None:
        functions = self.view.functions if self.view is not None else self.vocabulary.functions
        function = functions.get(call.operation)
        if function is None:
            if call.operation.split(".", 1)[0] not in self.vocabulary.function_namespaces or "." not in call.operation:
                self.add("expr.unknown_callee", where.node.id, callee=call.operation)
            elif self.view is not None:
                self.add("expr.unknown_function", where.node.id, function=call.operation)
            return
        if function.effect and not top:
            self.add("callback.nested", where.node.id, what=call.operation)
        if call.operation in _VERBS and top:
            self.check_query_effect(call, where)
        keys = {argument.key for argument in call.arguments or ()}
        unknown = sorted(key for key in keys if key is not None and key not in function.keys and not key.isdigit())
        if unknown:
            self.add("expr.arguments", where.node.id, function=call.operation, detail=f"has no argument {unknown[0]!r} ({', '.join(function.keys)})")
        missing = [key for key in function.required_keys if key not in keys]
        if missing:
            self.add("expr.arguments", where.node.id, function=call.operation, detail=f"needs {', '.join(missing)}")

    def check_query_effect(self, call: UtilCallInput, where: Where) -> None:
        """``query.refetch`` / ``query.fetch_more``: a declared query, named by a literal; fetch_more
        only of one that declares ``more``."""
        verb = _VERBS[call.operation]
        named = next((argument for argument in call.arguments or () if argument.key == "query"), None)
        if named is None:
            return  # expr.arguments says it is missing
        name = named.value_literal
        if not isinstance(name, str) or any(value is not None for value in (named.value_path, named.util_call, named.agent_call, named.value_list, named.value_dict)):
            self.add("query.effect_target", where.node.id, function=call.operation, verb=verb, what=_argument_what(named))
            return
        path = f"queries.{name}.{verb}()"
        if "queries" not in self.kind.roots:
            self.add("roots.not_in_kind", where.node.id, path=path, n=_article(self.scope.kind), kind=self.scope.kind, root="queries", roots=", ".join(sorted(self.kind.roots)))
            return
        query = self.queries.get(name)
        if query is None:
            self.add("query.unknown", where.node.id, path=path, name=name, names=", ".join(sorted(self.queries)) or "none")
            return
        if call.operation == FETCH_MORE_FUNCTION and query.more is None:
            self.add("query.fetch_more", where.node.id, path=path, name=name)

    def bind_let(self, name: str, items: ComponentPropInput | None, where: Where, scope: Mapping[str, Match]) -> Match:
        keyed = any(prop.key == FOREACH_KEY_PROP for prop in where.node.props or ())
        if items is not None and isinstance(items.static_value, list) and not keyed:
            if any(isinstance(item, (dict, list)) for item in items.static_value):
                self.add("foreach.position_key", where.node.id, let=name)
        if items is None or items.dynamic_value is None or items.dynamic_value.path is None:
            return open_match(name)
        resolved = self.resolve(items.dynamic_value.path, None, scope, owner=where.text, record=False)
        if resolved is None or is_open(resolved):
            return open_match(name)
        children = resolved.children or ()
        if len(children) == 1 and children[0].key == ITEM:
            item = children[0]
            if not keyed and (item.children or kind_name(item.kind) in _COMPOUND_KINDS):
                self.add("foreach.position_key", where.node.id, let=name)
            return item.model_copy(update={"key": name})
        if resolved.kind in (PortKind.LIST, None):
            return open_match(name)
        self.add("path.unknown", where.node.id, path=items.dynamic_value.path, detail=f"{FOREACH_ITEMS_PROP} of a foreach reads a list, not this {str(resolved.kind).lower()}")
        return open_match(name)

    def bind_into(self, name: str, call: AgentProbeInput) -> Match:
        dependency = self.dependencies.get(call.dependency)
        action = action_key_for(call)
        returns = None
        for demand in (dependency.action_dependencies or ()) if dependency is not None else ():
            if demand.key == action and demand.demand is not None:
                returns = demand.demand.return_matches
        return task_record_match(name, returns)

    # -- paths

    def resolve(self, path: str, where: Where | None, scope: Mapping[str, Match], *, owner: str, record: bool = True) -> Match:
        """What ``path`` holds, recording a finding (and returning None) when it holds nothing."""
        vocabulary = self.vocabulary
        node_id = where.node.id if where is not None else owner
        parts = path.split(".")
        root, rest = parts[0], parts[1:]
        if root in scope:
            if root in self.into:
                self.check_returns(path, rest, node_id)
            return self.descend(scope[root], rest, path, node_id)
        if root in self.locals:
            port = local_arg_port(self.locals[root])
            if port is None or kind_name(port.kind) in _OPAQUE_KINDS:
                return open_match(root)
            return self.descend(build_port_match(0, port).model_copy(update={"key": root, "at": None}), rest, path, node_id)
        if root == "event":
            if where is None or not where.callback:
                self.add("roots.event_outside_callback", node_id, path=path)
                return None
            return self.event_field(path, rest, where)
        if root == "error":
            if where is None or not where.in_error:
                self.add("roots.error_outside_boundary", node_id, path=path)
                return None
            return self.descend(record_match("error", vocabulary.records["error"].fields), rest, path, node_id)
        if root not in vocabulary.data_roots:
            detail = "a function namespace" if root in vocabulary.function_namespaces else None
            self.add("roots.unknown", node_id, path=path, root=root if detail is None else f"{root} ({detail})")
            return None
        if root not in self.kind.roots:
            self.add("roots.not_in_kind", node_id, path=path, n=_article(self.scope.kind), kind=self.scope.kind, root=root, roots=", ".join(sorted(self.kind.roots)))
            return None
        if root == "then":
            if rest:
                self.add("path.unknown", node_id, path=path, detail="then is a moment (null while live), it has no fields")
            return PortMatchInput(key="then", kind=PortKind.STRING, nullable=True)
        if root == "self":
            return self.agent(self.dependencies.get("self"), "self", rest, path, node_id)
        if root == "deps":
            if not rest:
                self.add("roots.whole", node_id, path=path, root="deps")
                return None
            dependency = self.dependencies.get(rest[0])
            if dependency is None:
                self.add("deps.unknown", node_id, path=path, key=rest[0], keys=", ".join(sorted(self.dependencies)) or "none", hint=dependency_hint(rest[0], self.scope.kind))
                return None
            return self.agent(dependency, rest[0], rest[1:], path, node_id)
        if root == "args":
            if not rest:
                self.add("roots.whole", node_id, path=path, root="args")
                return None
            if self.scope.args is not None and rest[0] not in self.scope.args:
                self.add("args.unknown", node_id, path=path, key=rest[0], keys=", ".join(self.scope.args) or "none")
                return None
            return open_match(path)
        if root == "task":
            self.check_returns(path, rest, node_id)
            return self.descend(task_record_match("task"), rest, path, node_id)
        if root == "viewer":
            return self.descend(record_match("viewer", vocabulary.records["viewer"].fields), rest, path, node_id)
        if root == "queries":
            if not rest:
                self.add("roots.whole", node_id, path=path, root="queries")
                return None
            if rest[0] not in self.queries:
                self.add("query.unknown", node_id, path=path, name=rest[0], names=", ".join(sorted(self.queries)) or "none")
                return None
            value = self.scope.query_values.get(rest[0])
            match = record_match(rest[0], vocabulary.records["query"].fields, {"value": value} if value is not None else None)
            return self.descend(match, rest[1:], path, node_id)
        if root == "subscriptions":
            if not rest:
                self.add("roots.whole", node_id, path=path, root="subscriptions")
                return None
            if rest[0] not in self.subscriptions:
                self.add("subscription.unknown", node_id, path=path, name=rest[0], names=", ".join(sorted(self.subscriptions)) or "none")
                return None
            return self.descend(record_match(rest[0], vocabulary.records["subscription"].fields), rest[1:], path, node_id)
        return open_match(path)

    def check_returns(self, path: str, rest: Sequence[str], node_id: str) -> None:
        """``<record>.returns.return0``: a return the action left unnamed (a warning)."""
        if len(rest) > 1 and rest[0] == RETURNS_FIELD and _UNNAMED_RETURN.fullmatch(rest[1]):
            self.add("returns.unnamed", node_id, path=path, port=rest[1])

    def event_field(self, path: str, rest: list[str], where: Where) -> Match:
        spec = self.view.component(where.node.component) if self.view is not None else None
        prop = spec.prop(where.key) if spec is not None else None
        if prop is None or prop.event is None or not rest:
            return open_match("event")
        fields = {event_field.key: event_field for event_field in prop.event}
        if rest[0] not in fields:
            self.add("callback.event_field", where.node.id, field=rest[0], key=where.key, component=where.node.component, fields=", ".join(fields) or "none")
            return None
        return open_match(rest[0])

    def agent(self, dependency: AgentDependencyInput | None, key: str, rest: list[str], path: str, node_id: str) -> Match:
        """What a path below an agent (``self``, ``deps.<key>``) holds."""
        vocabulary = self.vocabulary
        if not rest:
            return open_match(key)
        head, tail = rest[0], rest[1:]
        if head in vocabulary.agent_fields:
            if tail:
                self.add("path.unknown", node_id, path=path, detail=f"{head} of an agent has no fields")
            return PortMatchInput(key=head, kind=PortKind.STRING)
        if head == vocabulary.tasks_segment:
            if not tail:
                self.add("roots.whole", node_id, path=path, root=f"{key}.tasks")
                return None
            action = tail[0]
            returns = None
            if dependency is not None and dependency.action_dependencies is not None:
                demand = next((demand for demand in dependency.action_dependencies if demand.key == action), None)
                if demand is None:
                    self.add("deps.unknown_action", node_id, path=path, key=key, action=action, actions=", ".join(sorted(d.key for d in dependency.action_dependencies)) or "none")
                    return None
                returns = demand.demand.return_matches if demand.demand is not None else None
            self.check_returns(path, tail[1:], node_id)
            return self.descend(task_record_match(action, returns), tail[1:], path, node_id)
        if head == vocabulary.assets_segment:
            if len(tail) != 1:
                self.add("deps.asset", node_id, path=path)
                return None
            if key == "self" and self.scope.assets is not None and tail[0] not in self.scope.assets:
                self.add("deps.asset", node_id, path=path)
                return None
            return PortMatchInput(key=tail[0], kind=PortKind.STRING)
        if dependency is None or dependency.state_dependencies is None:
            return open_match(path)
        state = next((demand for demand in dependency.state_dependencies if demand.key == head), None)
        if state is None:
            self.add("deps.unknown_state", node_id, path=path, key=key, state=head, states=", ".join(sorted(d.key for d in dependency.state_dependencies)) or "none")
            return None
        return self.descend(_state_match(state), tail, path, node_id)

    def descend(self, match: Match, rest: Sequence[str], path: str, node_id: str) -> Match:
        """Follow ``rest`` down ``match``'s children."""
        current = match
        for index, part in enumerate(rest):
            if current is None or is_open(current):
                return current
            children = current.children or ()
            child = next((candidate for candidate in children if candidate.key == part), None)
            if child is None and len(children) == 1 and children[0].key == ITEM:
                child = children[0]
            if child is None:
                available = sorted(candidate.key for candidate in children if candidate.key not in (None, ITEM))
                where = ".".join(rest[:index]) or current.key or "it"
                detail = f"{part!r} does not exist in {where}" + (f" (it has: {', '.join(available)})" if available else " (it has no fields)")
                self.add("path.unknown", node_id, path=path, detail=detail)
                return None
            current = child
        return current


def dependency_hint(key: str, kind: str) -> str:
    """What a blok of ``kind`` that reads an undeclared ``deps.<key>`` does about it.

    - custom and task bloks declare it, to copy as it is: ``dependencies=[AgentDependencyInput(key="laser")]``;
    - an action blok's dependencies are its action's: ``add a dependency parameter `laser` to the action``;
    - an agent blok has none: ``agent bloks read only `self```.
    """
    if kind in ("action", "action_logo"):
        return f"add a dependency parameter `{key}` to the action"
    if kind == "agent":
        return "agent bloks read only `self`"
    return f'dependencies=[AgentDependencyInput(key="{key}")]'


def literal_hint(key: str, value: str, kind: str) -> str:
    """How a string on a typed prop is written as the literal it meant: ``fullWidth={true}``."""
    written = value.strip()
    if kind == "BOOL":
        literal = written.lower() if written.lower() in ("true", "false") else "true"
    else:
        try:
            number = float(written)
        except ValueError:
            number = 0.0
        if kind == "INT":
            literal = str(int(number))
        else:
            literal = str(int(number)) if number.is_integer() and "." not in written else repr(number)
    return f"{key}={{{literal}}}"


def _article(kind: str) -> str:
    return "n" if kind[:1] in "aeiou" else ""


def _argument_what(argument: ActionArgumentInput) -> str:
    if argument.value_path is not None:
        return f"the path {argument.value_path}"
    if argument.util_call is not None:
        return f"a call of {argument.util_call.operation}"
    if argument.agent_call is not None:
        return "a task call"
    if argument.value_list is not None or argument.value_dict is not None:
        return "a list or object"
    return f"the value {argument.value_literal!r}"


def _what(prop: ComponentPropInput) -> str:
    if prop.agent_call is not None:
        return "a task call"
    if prop.util_call is not None:
        return prop.util_call.operation
    if prop.dynamic_value is not None:
        return f"the path {prop.dynamic_value.path}"
    return f"the value {prop.static_value!r}"


def _class_literals(call: UtilCallInput) -> list[str]:
    """Every literal string of a ``str.classes`` call: list entries, object keys, branches."""
    literals: list[str] = []

    def visit_arguments(arguments: Iterable[object]) -> None:
        for argument in arguments:
            if not isinstance(argument, ActionArgumentInput):
                continue
            if isinstance(argument.value_literal, str):
                literals.append(argument.value_literal)
            for nested in argument.value_dict or ():
                if nested.key:
                    literals.append(nested.key)
            if argument.util_call is not None:
                visit_arguments(argument.util_call.arguments or ())
            visit_arguments(argument.value_list or ())

    visit_arguments(call.arguments or ())
    return literals


def _state_match(state: StateDependencyInput) -> PortMatchInput:
    return PortMatchInput(key=state.key, kind=PortKind.DICT, children=state.demand.matches if state.demand else None)


def check_logo(tree: ComponentNodeInput, demo: BlokDemoInput | None = None) -> list[Finding]:
    """A logo is an icon: few nodes, shallow, little path data, and loops over few items."""
    limits = load_vocabulary().limits
    findings: list[Finding] = []
    nodes = 0
    path_data = 0

    def visit(current: ComponentNodeInput, depth: int) -> None:
        nonlocal nodes, path_data
        nodes += 1
        if depth > limits.logo_max_depth:
            findings.append(Finding.of("logo.complexity", current.id, detail=f"a logo nests at most {limits.logo_max_depth} deep"))
        for prop in current.props or ():
            if prop.key in limits.logo_path_data_props and isinstance(prop.static_value, str):
                path_data += len(prop.static_value)
            if current.component == "foreach" and prop.key == FOREACH_ITEMS_PROP:
                items = _demo_items(prop, demo)
                if items > limits.logo_max_foreach_items:
                    findings.append(
                        Finding.of("logo.complexity", current.id, detail=f"a foreach in a logo draws at most {limits.logo_max_foreach_items} items, its demo has {items}")
                    )
        for child in current.children or ():
            visit(child, depth + 1)

    visit(tree, 1)
    if nodes > limits.logo_max_nodes:
        findings.append(Finding.of("logo.complexity", tree.id, detail=f"a logo has at most {limits.logo_max_nodes} components, this one {nodes}"))
    if path_data > limits.logo_max_path_data:
        findings.append(Finding.of("logo.complexity", tree.id, detail=f"a logo holds at most {limits.logo_max_path_data} characters of path data, this one {path_data}"))
    return findings


def _demo_items(prop: ComponentPropInput, demo: BlokDemoInput | None) -> int:
    if isinstance(prop.static_value, list):
        return len(prop.static_value)
    path = prop.dynamic_value.path if prop.dynamic_value is not None else None
    if path is None or demo is None:
        return 0
    parts = path.split(".")
    roots: dict[str, JSON] = {"task": demo.task, "args": demo.args}
    value: JSON
    if parts[0] == "self":
        deps = demo.deps if isinstance(demo.deps, dict) else {}
        value, parts = deps.get("self"), parts[1:]
    elif parts[0] == "deps":
        value, parts = demo.deps, parts[1:]
    else:
        value, parts = roots.get(parts[0]), parts[1:]
    for part in parts:
        if not isinstance(value, dict) or part not in value:
            return 0
        value = value[part]
    return len(value) if isinstance(value, list) else 0


def check_blok_tree(tree: ComponentNodeInput, scope: BlokScope, view: BlokView | None = None) -> list[Finding]:
    """Every finding of ``tree`` against ``scope`` and ``view``: errors and warnings, in tree order.

    The scope is resolved first (:func:`~arkitekt_spec.declare.blok.infer.resolve_scope`): the
    kinds of its locals.

    Args:
        tree: The blok's root.
        scope: What it declares beside its tree (:class:`BlokScope`).
        view: The catalogs that draw it; ``None`` skips every catalog rule (component names,
            props, placement, callback props, event fields, unknown functions).
    """
    from arkitekt_spec.declare.blok.infer import resolve_scope

    checker = _Checker(resolve_scope(tree, scope, view), view)
    checker.check_declarations(tree)
    walk_tree(tree, checker)
    if scope.drawn_in == LOGO:
        checker.findings.extend(check_logo(tree, scope.demo))
    checker.finish()
    return checker.findings


def validate_blok(
    tree: ComponentNodeInput,
    scope: BlokScope | None = None,
    view: BlokView | None = None,
    *,
    mode: BlokCheckMode = "warning",
) -> list[Finding]:
    """Refuse ``tree`` on its first error (or, in strict mode, its first finding); return its warnings.

    Raises:
        BlokError: With the code of the first error.
    """
    findings = check_blok_tree(tree, scope or BlokScope(), view)
    for finding in findings:
        if finding.level == "error" or mode == "strict":
            raise BlokError.from_finding(finding)
    return findings


def validate_bsx(
    source: str | ComponentNodeInput,
    scope: BlokScope | None = None,
    view: BlokView | None = None,
    *,
    mode: BlokCheckMode = "warning",
) -> list[Finding]:
    """Parse ``source`` (if it is BSX) and :func:`validate_blok` it against the Orkestrator's view.

    A finding about a node of a parsed source carries its ``line`` and ``column``, a refusal too.

    Example:
        >>> validate_bsx('<Html><Card><Button>Go</Button></Card></Html>')
        []
    """
    from arkitekt_spec.blok.bsx import parse_bsx_with_spans

    resolved = view or default_view()
    if not isinstance(source, str):
        return validate_blok(source, scope, resolved, mode=mode)
    tree, spans = parse_bsx_with_spans(source, view=resolved)
    findings = [place(finding, spans) for finding in check_blok_tree(tree, scope or BlokScope(), resolved)]
    for finding in findings:
        if finding.level == "error" or mode == "strict":
            raise BlokError.from_finding(finding)
    return findings


def place(finding: Finding, spans: Mapping[str, tuple[int, int]]) -> Finding:
    """``finding`` at the line and column of the node it names, when ``spans`` (from
    :func:`~arkitekt_spec.blok.bsx.parse_bsx_with_spans`) has it."""
    if finding.path is None or finding.path not in spans:
        return finding
    return finding.placed(*spans[finding.path])


def resolve_state_reference(
    dependency: str | None,
    state_path: str,
    *,
    dependencies: Iterable[AgentDependencyInput],
    own_states: Iterable[StateImplementationInput],
    context: str,
) -> None:
    """Validate a ``withStateChoices`` state reference: through its dependency, or the agent's own states.

    Raises:
        ValueError: If the reference cannot be resolved.
    """
    parts = [part for part in state_path.split(".") if part]
    if not parts:
        return
    if dependency is None:
        own = {state.interface: state for state in own_states}
        state = own.get(parts[0])
        if state is None:
            raise ValueError(
                f"Unknown self state reference '{state_path}' in {context}: no own state '{parts[0]}'. Available states: {sorted(own)}"
            )
        _follow(PortMatchInput(key=parts[0], children=build_port_matches(state.definition.ports)), parts[1:], state_path, context)
        return
    by_key = {item.key: item for item in dependencies}
    found = by_key.get(dependency)
    if found is None:
        raise ValueError(
            f"State choice widget in {context} references unknown dependency '{dependency}'. Available dependencies: {sorted(by_key)}"
        )
    demands = {demand.key: demand for demand in found.state_dependencies or ()}
    demand = demands.get(parts[0])
    if demand is None:
        raise ValueError(
            f"Unknown nested reference '{state_path}' in {context}: state '{parts[0]}' does not exist on dependency '{dependency}'. Available states: {sorted(demands)}"
        )
    _follow(_state_match(demand), parts[1:], state_path, context)


def _follow(match: PortMatchInput, parts: Sequence[str], path: str, context: str) -> None:
    current = match
    for part in parts:
        children = current.children or ()
        child = next((candidate for candidate in children if candidate.key == part), None)
        if child is None and len(children) == 1 and children[0].key == ITEM:
            child = children[0]
        if child is None:
            raise ValueError(f"Unknown nested reference '{path}' in {context}: '{part}' does not exist")
        current = child


__all__ = [
    "OPEN_IDENTIFIER",
    "BlokScope",
    "blok_context",
    "check_blok_tree",
    "check_logo",
    "is_open",
    "open_match",
    "record_match",
    "resolve_state_reference",
    "task_record_match",
    "validate_blok",
    "validate_bsx",
]
