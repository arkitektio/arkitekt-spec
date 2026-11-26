"""Build an app's registry from what it declares, when it is snapshotted.

Three steps:

1. **Walk.** Every annotation of what the app offers (actions, hooks, per-app
   classes, the provider) is followed through models, states and protocols. A
   class marked ``@client`` is a client the app uses; one marked ``@structure``
   brings its owning client; ``@model``/``@state``/``@context``/``@declare``/
   ``@interactions`` classes are taken in. Nothing else is: an unmarked class
   fails where its port is built, with that converter's explanation. A client
   brings no structures of its own accord: only those the annotations reach.
2. **Build.** Into a fresh :class:`AppRegistry`, in an order that never depends on
   how the app was written: clients and their structures, then models, contexts,
   states, protocols, interactions, actions, hooks, bloks. A per-app declaration
   (``@app.state``) wins over a class's own mark.
3. **Validate.** Every problem from both steps is raised at once, as an
   :class:`AppDeclarationError`.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal, get_args, get_origin, get_type_hints

from arkitekt_spec.actions import Effects
from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.blok.origin import DeclaredAt
from arkitekt_spec.declare.bundle import ACTION_METHODS, HOOK_METHODS, Bundle, Entry
from arkitekt_spec.declare.client import ClientDeclaration, client_of
from arkitekt_spec.declare.definition.utils import interface_name
from arkitekt_spec.declare.errors import AppDeclarationError
from arkitekt_spec.declare.markers import (
    CONTEXT,
    INTERACTIONS,
    MODEL,
    PROTOCOL,
    STATE,
    marker_of,
)
from arkitekt_spec.declare.provider import Provider
from arkitekt_spec.declare.structures.convert import AppNamespace
from arkitekt_spec.declare.structures.declare import StructureDeclaration, structure_of

#: What a per-app entry declares a class as, by the registry method it is built with.
_KIND_OF_METHOD = {
    "model": MODEL,
    "state": STATE,
    "context": CONTEXT,
    "declare": PROTOCOL,
    "register_interactions": INTERACTIONS,
}
#: Built in this order: each kind's ports may name the kinds before it.
_CLASS_KINDS = (MODEL, CONTEXT, STATE, PROTOCOL, INTERACTIONS)


@dataclass
class _Problem:
    error: Exception
    what: str
    at: DeclaredAt | None


@dataclass
class _Walk:
    """What the annotations of an app reach."""

    clients: list[ClientDeclaration[Any]] = field(default_factory=list)
    structures: list[StructureDeclaration] = field(default_factory=list)
    classes: dict[str, list[type]] = field(
        default_factory=lambda: {kind: [] for kind in _CLASS_KINDS}
    )
    seen: set[type] = field(default_factory=set)
    explicit: dict[tuple[str, type], Entry] = field(default_factory=dict)

    def client(self, declaration: ClientDeclaration[Any]) -> None:
        if declaration not in self.clients:
            self.clients.append(declaration)

    def visit(self, annotation: object) -> None:
        """Follow an annotation to the classes it names."""
        origin = get_origin(annotation)
        if origin is Annotated:
            inner, *metadata = get_args(annotation)
            self.visit(inner)
            # `Annotated[str, Flow]`: a value passed by reference to a structure.
            for item in metadata:
                if structure_of(item) is not None:
                    self.visit_class(item)
        elif origin is Literal:
            return
        elif origin is not None:
            if isinstance(origin, type):
                self.visit_class(origin)
            for arg in get_args(annotation):
                if arg is not Ellipsis:
                    self.visit(arg)
        elif isinstance(annotation, type):
            self.visit_class(annotation)

    def visit_hints(self, target: object) -> None:
        for hint in _hints(target).values():
            self.visit(hint)

    def kind_of(self, cls: type) -> str | None:
        for kind in _CLASS_KINDS:
            if (kind, cls) in self.explicit or marker_of(cls, kind) is not None:
                return kind
        return None

    def visit_class(self, cls: type) -> None:
        if cls in self.seen:
            return
        self.seen.add(cls)
        declaration = client_of(cls)
        if declaration is not None:
            self.client(declaration)
            return
        structure = structure_of(cls)
        if structure is not None:
            # A fragment is fetched whole by its client: nothing inside it is a port.
            if structure.owner is not None:
                self.client(structure.owner)
            if structure not in self.structures:
                self.structures.append(structure)
            return
        kind = self.kind_of(cls)
        if kind is None:
            return
        # What it holds first, so a model is built after the models in its fields.
        self.visit_hints(cls)
        if kind in (PROTOCOL, INTERACTIONS):
            for name, member in vars(cls).items():
                if not name.startswith("_") and callable(member):
                    self.visit_hints(member)
        self.classes[kind].append(cls)


def _hints(target: object) -> dict[str, Any]:
    """The annotations of a function or class; unreadable ones fail later, at their port."""
    try:
        return get_type_hints(target, include_extras=True)
    except Exception:  # noqa: BLE001 -- reported where the port is built
        return {}


def _at(obj: object) -> DeclaredAt | None:
    code = getattr(obj, "__code__", None)
    if code is None:
        return None
    return DeclaredAt(
        code.co_filename, code.co_firstlineno, getattr(obj, "__module__", "")
    )


#: Builders that report findings at a line: handed the entry's, not the assembler's caller's.
_TAKES_LINE = {
    "register_action": "declared_at",
    "register_workflow": "declared_at",
    "register_blok": "at",
    "register_interactions": "at",
}


def _replay(registry: AppRegistry, entry: Entry) -> None:
    """Build one entry into ``registry``."""
    line = _TAKES_LINE.get(entry.method)
    if line is not None and entry.declared_at is not None:
        entry = Entry(
            entry.method,
            entry.args,
            {**entry.kwargs, line: entry.declared_at},
            entry.declared_at,
        )
    if entry.method == "declare":
        registry.declare(**entry.kwargs)(entry.target)
    elif entry.method == "register_as_structure":
        registry.structure_registry.register_as_structure(*entry.args, **entry.kwargs)
    else:
        getattr(registry, entry.method)(*entry.args, **entry.kwargs)


def _build_marked(registry: AppRegistry, kind: str, cls: type) -> None:
    """Build a class from its own mark."""
    mark = marker_of(cls, kind)
    if kind == MODEL:
        registry.model(cls, identifier=mark.identifier, description=mark.description)
    elif kind == STATE:
        registry.state(
            cls,
            name=mark.name,
            required_locks=list(mark.required_locks) or None,
            persist=mark.persist,
        )
    elif kind == CONTEXT:
        registry.context(cls, name=mark.name, locks=list(mark.locks) or None)
    elif kind == PROTOCOL:
        registry.declare(
            mark.app,
            auto_resolvable=mark.auto_resolvable,
            min=mark.min,
            max=mark.max,
            version=mark.version,
        )(cls)
    else:
        registry.register_interactions(cls, getattr(mark, "declared_at", None))


def assemble(
    bundle: Bundle,
    *,
    namespace: AppNamespace | None = None,
    root: Path | None = None,
    app_context: type | None = None,
    default_effects: Effects | None = None,
    provider: Provider[Any] | None = None,
) -> AppRegistry:
    """Build the registry ``bundle`` declares: walked, built and validated, in that order.

    Args:
        bundle: What the app declares (an app is one).
        namespace: The app's namespace, which derived identifiers are made in.
        root: The app's directory, which asset paths are relative to.
        app_context: The class of the app's context, if it has one.
        default_effects: What an action that makes no claim of its own does again.
        provider: What builds the agent, taken in with the clients it uses.

    Returns:
        The registry: not frozen, so a run can still snapshot it.

    Raises:
        AppDeclarationError: Every problem found, each noted with where it was declared.
    """
    problems: list[_Problem] = []
    entries = list(bundle.entries())

    def attempt(what: str, at: DeclaredAt | None, build: Callable[[], object]) -> None:
        try:
            build()
        except Exception as e:  # noqa: BLE001 -- every problem is reported, together
            problems.append(_Problem(e, what, at))

    # 1. Walk.
    walk = _Walk()
    for entry in entries:
        kind = _KIND_OF_METHOD.get(entry.method)
        if kind is not None:
            walk.explicit[(kind, entry.target)] = entry
    for cls in bundle.clients():
        declaration = client_of(cls)
        if declaration is not None:
            walk.client(declaration)
    if provider is not None:
        for cls in provider.clients:
            declaration = client_of(cls)
            if declaration is not None:
                walk.client(declaration)
        for injection in provider.injections:
            if injection.cls is not None:
                walk.visit_class(injection.cls)
    if app_context is not None:
        walk.seen.add(app_context)
    # Per-app classes first, so each is taken in even if nothing offered names it.
    for entry in entries:
        if entry.method in _KIND_OF_METHOD:
            walk.visit_class(entry.target)
    for entry in entries:
        if entry.method in ACTION_METHODS or entry.method in HOOK_METHODS:
            walk.visit_hints(entry.target)
        elif entry.method == "register_blok":
            for annotation in (entry.kwargs.get("args") or {}).values():
                walk.visit(annotation)
        elif entry.method == "register_as_structure":
            for name in ("aexpand", "ashrink", "aexpand_many"):
                function = entry.kwargs.get(name)
                if function is not None:
                    walk.visit_hints(function)

    # 2. Build.
    registry = AppRegistry()
    registry.structure_registry.namespace = namespace
    if root is not None:
        registry.root = root
    if default_effects is not None:
        registry.default_effects = default_effects
    if app_context is not None:
        attempt("the app context", None, lambda: registry.app_context(app_context))
    if provider is not None:
        attempt(
            f"provider '{provider.name}'",
            _at(provider.function),
            lambda: registry.use_provider(provider),
        )

    for declaration in walk.clients:
        attempt(
            f"client '{declaration.name}'",
            declaration.declared_at,
            lambda d=declaration: registry.use_client(d),
        )

    def entries_of(methods: Iterable[str]) -> list[Entry]:
        wanted = set(methods)
        return [entry for entry in entries if entry.method in wanted]

    for entry in entries_of(("declare_ui_catalog", "register_asset")):
        attempt(
            entry.describe(), entry.declared_at, lambda e=entry: _replay(registry, e)
        )

    explicit_structures = {
        entry.args[0] if entry.args else entry.kwargs.get("cls")
        for entry in entries_of(("register_as_structure",))
    }

    def build_structure(s: StructureDeclaration, client: str | None) -> None:
        registry.structure_registry.register_as_structure(
            s.cls,
            identifier=s.identifier,
            aexpand=s.aexpand,
            ashrink=s.ashrink,
            aexpand_many=s.aexpand_many,
            description=s.description,
            default_widget=s.default_widget,
            default_returnwidget=s.default_returnwidget,
            describe=s.describe,
            client=client,
        )

    # Only what the app's annotations reach: an untyped call returns references
    # (arkitekt_runtime.ref), so nothing else is ever expanded through this registry.
    for s in walk.structures:
        if s.cls not in explicit_structures:
            attempt(
                f"structure '{s.identifier}'",
                s.declared_at,
                lambda s=s: build_structure(s, s.owner.name if s.owner else None),
            )
    for entry in entries_of(("register_as_structure", "register_memory_structure")):
        attempt(
            entry.describe(), entry.declared_at, lambda e=entry: _replay(registry, e)
        )

    for kind in _CLASS_KINDS:
        for cls in walk.classes[kind]:
            explicit = walk.explicit.get((kind, cls))
            if explicit is not None:
                attempt(
                    explicit.describe(),
                    explicit.declared_at,
                    lambda e=explicit: _replay(registry, e),
                )
            else:
                mark = marker_of(cls, kind)
                attempt(
                    f"{kind.strip('_').removeprefix('arkitekt_')} {cls.__qualname__}",
                    getattr(mark, "declared_at", None),
                    lambda k=kind, c=cls: _build_marked(registry, k, c),
                )

    interfaces: dict[str, Entry] = {}
    for entry in entries_of(ACTION_METHODS):
        interface = (
            entry.kwargs.get("interface")
            or entry.kwargs.get("key")
            or interface_name(entry.target)
        )
        first = interfaces.setdefault(interface, entry)
        if first is not entry:
            problems.append(
                _Problem(
                    ValueError(
                        f"Two actions are offered at '{interface}' ({first.describe()} "
                        f"and {entry.describe()}): give one its own key=."
                    ),
                    entry.describe(),
                    entry.declared_at,
                )
            )
            continue
        attempt(
            entry.describe(),
            entry.declared_at or _at(entry.target),
            lambda e=entry: _replay(registry, e),
        )

    for entry in entries_of(HOOK_METHODS):
        attempt(
            entry.describe(),
            entry.declared_at or _at(entry.target),
            lambda e=entry: _replay(registry, e),
        )
    for entry in entries_of(("register_blok",)):
        attempt(
            entry.describe(), entry.declared_at, lambda e=entry: _replay(registry, e)
        )

    # 3. Validate what was built.
    if not problems:
        attempt("the app", None, registry.validate)
    if not problems:
        _check_reads(registry, interfaces, attempt)
    if problems:
        raise _error(problems)
    return registry


def _check_reads(
    registry: AppRegistry,
    interfaces: dict[str, Entry],
    attempt: Callable[[str, DeclaredAt | None, Callable[[], object]], None],
) -> None:
    """What only the whole app answers: what each blok and port blok reads of ``self``, and each
    own-state ``withStateChoices`` path. Also run by ``to_implement_agent_input``."""
    from arkitekt_spec.declare.blok.port import check_action_port_blok_reads
    from arkitekt_spec.declare.blok.registry import build_declared_blok
    from arkitekt_spec.declare.definition.checks import check_implementation

    own_states = tuple(registry.states.values())
    for interface, implementation in registry.implementations.items():
        entry = interfaces.get(interface)
        at = (entry.declared_at or _at(entry.target)) if entry is not None else None
        what = entry.describe() if entry is not None else f"action '{interface}'"
        attempt(
            what,
            at,
            lambda i=interface, impl=implementation: (
                check_action_port_blok_reads(registry, i),
                check_implementation(impl, own_states),
            ),
        )
    for key, declaration in registry.registered_bloks.items():
        attempt(
            f"blok '{key}'",
            declaration.declared_at,
            lambda k=key, d=declaration: build_declared_blok(registry, k, d),
        )


def _error(problems: list[_Problem]) -> AppDeclarationError:
    def where(problem: _Problem) -> tuple[str, int]:
        return (problem.at.filename, problem.at.lineno) if problem.at else ("", 0)

    members: list[Exception] = []
    lines: list[str] = []
    for problem in sorted(problems, key=where):
        place = f"{problem.at.filename}:{problem.at.lineno}: " if problem.at else ""
        note = f"{place}{problem.what}"
        problem.error.add_note(note)
        problem.error.declared_at = problem.at  # type: ignore[attr-defined]
        members.append(problem.error)
        lines.append(f"  {note}: {problem.error}")
    count = len(members)
    return AppDeclarationError(
        f"The app is not valid ({count} problem{'s' if count != 1 else ''}):\n"
        + "\n".join(lines),
        members,
    )


__all__ = ["assemble"]
