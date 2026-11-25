"""A bundle: what an app offers, written down, assembled later.

Declaring an action, a hook, a blok or a per-app state records an *entry*; nothing
is built. ``assemble`` builds every entry into a registry at once, when the app is
snapshotted, so the order things are declared in no longer matters and every
problem is reported together.

An app is a bundle. A package that offers actions of its own (fluss's ``run_flow``)
ships one for apps to take in with ``include``; a client never brings actions.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, ParamSpec, TypeVar, cast

from arkitekt_spec.declare.blok.origin import DeclaredAt, declared_at
from arkitekt_spec.declare.client import client_of
from arkitekt_spec.declare.definition.utils import interface_name
from arkitekt_spec.declare.register import WrappedFunction

P = ParamSpec("P")
R = TypeVar("R")
T = TypeVar("T")

ACTION_METHODS = frozenset({"register_action", "register_workflow"})
HOOK_METHODS = frozenset({"startup", "shutdown", "background", "lifecycle"})


@dataclass(eq=False)
class Entry:
    """One declaration: the registry method it is built with, and what it is called with."""

    method: str
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    declared_at: DeclaredAt | None = None

    @property
    def target(self) -> Any:  # noqa: ANN401 -- the function or class declared
        """What the entry declares: its first argument."""
        return self.args[0] if self.args else None

    def describe(self) -> str:
        """How a problem with it is named: ``action 'segment'``, ``state CameraState``."""
        target = self.target
        name = getattr(target, "__qualname__", None) or repr(target)
        if self.method in ACTION_METHODS:
            return f"action '{self.kwargs.get('key') or getattr(target, '__name__', name)}'"
        if self.method in HOOK_METHODS:
            return f"{self.method} hook '{self.kwargs.get('name') or name}'"
        if self.method == "register_blok":
            return f"blok '{target}'"
        return f"{self.method} {name}"


class Bundle:
    """Entries, the clients used without being named in an annotation, and bundles taken in."""

    def __init__(self) -> None:
        self._entries: list[Entry] = []
        self._clients: list[type] = []
        self._included: list[Bundle] = []
        self._version = 0

    # ------------------------------------------------------------------ #
    # Recording                                                          #
    # ------------------------------------------------------------------ #

    def _record(
        self, method: str, *args: Any, at: DeclaredAt | None = None, **kwargs: Any
    ) -> Entry:
        """Write down a declaration, built later with ``AppRegistry.<method>``."""
        entry = Entry(method, args, kwargs, at if at is not None else declared_at())
        self._entries.append(entry)
        self._version += 1
        return entry

    def _offer(
        self, method: str, func: Callable[P, R], options: dict[str, Any]
    ) -> WrappedFunction[P, R]:
        """Record an action entry, and hand the function back knowing its interface."""
        self._record(method, func, **options)
        return WrappedFunction(func, _interface_of(func, options))

    def register_action(
        self, func: Callable[P, R], /, **options: Any
    ) -> WrappedFunction[P, R]:
        """Offer ``func`` as an action; takes the options of ``AppRegistry.register_action``.

        Returns:
            The function, still callable as itself, knowing its interface.
        """
        return self._offer("register_action", func, options)

    def register_workflow(
        self, func: Callable[P, R], /, **options: Any
    ) -> WrappedFunction[P, R]:
        """Offer ``func`` as a workflow: an action that may call others and is resumed."""
        return self._offer("register_workflow", func, options)

    def register_client(self, *classes: type) -> None:
        """Use these clients even where no annotation names them (a blok's query, a call).

        Raises:
            TypeError: If a class is not declared with ``@client``.
        """
        for cls in classes:
            if client_of(cls) is None:
                raise TypeError(
                    f"{cls!r} is not a client: declare it with @client on its class."
                )
            if cls not in self._clients:
                self._clients.append(cls)
                self._version += 1

    def include(self, *bundles: "Bundle") -> None:
        """Take in what other bundles offer: ``app.include(fluss_actions)``.

        Taken by reference: what is declared on them later is included too.
        """
        for bundle in bundles:
            if not isinstance(bundle, Bundle):
                raise TypeError(f"{bundle!r} is not a bundle.")
            if bundle is not self and bundle not in self._included:
                self._included.append(bundle)
                self._version += 1

    # ------------------------------------------------------------------ #
    # Reading                                                            #
    # ------------------------------------------------------------------ #

    def bundles(self) -> list["Bundle"]:
        """This bundle and every one it includes, each once, this one first."""
        seen: list[Bundle] = []

        def visit(bundle: Bundle) -> None:
            if any(b is bundle for b in seen):
                return
            seen.append(bundle)
            for included in bundle._included:
                visit(included)

        visit(self)
        return seen

    def entries(self) -> Iterator[Entry]:
        """Every entry, this bundle's first, then each included one's."""
        for bundle in self.bundles():
            yield from bundle._entries

    def clients(self) -> list[type]:
        """The clients named here and in every included bundle."""
        named: list[type] = []
        for bundle in self.bundles():
            named.extend(c for c in bundle._clients if c not in named)
        return named

    def fingerprint(self) -> tuple[tuple[int, int], ...]:
        """Changes whenever anything here or in an included bundle is declared."""
        return tuple((id(b), b._version) for b in self.bundles())

    @property
    def interfaces(self) -> list[str]:
        """The interfaces of the actions offered, in declaration order."""
        return [
            _interface_of(entry.target, entry.kwargs)
            for entry in self.entries()
            if entry.method in ACTION_METHODS
        ]

    def is_empty(self) -> bool:
        """Whether nothing is offered: no action, state, hook or blok."""
        return not any(
            entry.method in ACTION_METHODS
            or entry.method in HOOK_METHODS
            or entry.method in ("state", "register_blok")
            for entry in self.entries()
        )


def _interface_of(func: Callable[..., Any], options: dict[str, Any]) -> str:
    return cast(
        str,
        options.get("interface") or options.get("key") or interface_name(func),
    )


__all__ = ["ACTION_METHODS", "HOOK_METHODS", "Bundle", "Entry"]
