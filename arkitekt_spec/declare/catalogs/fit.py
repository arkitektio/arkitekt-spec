"""Whether a value of one port fits a prop typed by another: the catalog's type check.

``fits(source, target)`` answers for what a path, a local or a literal holds (``source``)
against what a prop, an argument or a binding takes (``target``):

- ``ok``: it fits;
- ``open``: nothing can be said (an open or untyped side) -- never a finding;
- ``mismatch``: both are concrete and do not overlap, with the reason.

The rules, in order:

1. an open side is ``open``;
2. a UNION target takes a source that fits one variant; a UNION source fits when every variant does;
3. widening: INT and QUANTITY fit FLOAT; ENUM and DATE fit STRING; MODEL fits DICT;
   a STRUCTURE fits a DICT openly (its record is read, not its fields);
4. the same kind recurses: LIST items, DICT values or named keys, MODEL fields by key,
   STRUCTURE identifiers (``*`` takes any);
5. choices: a source with choices fits when each is one of the target's.

``nullable`` is not compared: a path may be null whatever its port says, and a literal null
is checked on its own (:func:`literal_port`).

A ``binding`` check is the other direction of the same question, for the local a control
writes: the BINDING's child is what the control writes and the local must take it (of a
UNION, one variant: the control writes one, by how it is set). Its kinds
are those of the vocabulary's ``bind_kinds`` (per kind the control writes, the local kinds that
take it), so a FLOAT slider may write an INT or a QUANTITY local.

Pure, and replayed by bsx and takt from the corpus (``fits`` cases).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from arkitekt_spec.declare.catalogs.ports import (
    ITEM_KEY,
    OPEN_IDENTIFIER,
    CatalogPort,
    Choice,
)

FitStatus = Literal["ok", "open", "mismatch"]


@dataclass(frozen=True)
class Fit:
    status: FitStatus
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.status != "mismatch"


OK = Fit("ok")
OPEN = Fit("open")

_WIDENS: Mapping[str, frozenset[str]] = {
    "FLOAT": frozenset({"INT", "QUANTITY"}),
    "STRING": frozenset({"ENUM", "DATE"}),
    "DICT": frozenset({"MODEL"}),
}
_IDENTIFIED = frozenset({"STRUCTURE", "MEMORY_STRUCTURE", "INTERFACE"})


def _name(port: CatalogPort) -> str:
    if port.kind in _IDENTIFIED:
        return f"{port.kind}({port.identifier})"
    return port.kind or "ANY"


def _join(fits: list[Fit]) -> Fit:
    """All of them: the first mismatch, else open if any is, else ok."""
    for fit in fits:
        if fit.status == "mismatch":
            return fit
    return OPEN if any(fit.status == "open" for fit in fits) else OK


def _choices_fit(
    source: tuple[Choice, ...] | None, target: tuple[Choice, ...] | None
) -> Fit:
    if target is None or source is None:
        return OK
    allowed = {choice.value for choice in target}
    stray = [choice.value for choice in source if choice.value not in allowed]
    if stray:
        return Fit(
            "mismatch",
            f"{', '.join(map(repr, stray))} not among {', '.join(map(repr, sorted(allowed, key=str)))}",
        )
    return OK


def fits(source: CatalogPort, target: CatalogPort) -> Fit:
    """Whether a value of ``source`` fits where ``target`` is taken."""
    if source.is_open or target.is_open:
        return OPEN
    if target.kind == "UNION":
        results = [fits(source, child.port) for child in target.children]
        if any(result.status == "ok" for result in results):
            return OK
        if any(result.status == "open" for result in results):
            return OPEN
        return Fit(
            "mismatch",
            f"{_name(source)} is none of {', '.join(_name(c.port) for c in target.children)}",
        )
    if source.kind == "UNION":
        return _join([fits(child.port, target) for child in source.children])

    kind, wanted = source.kind, target.kind
    if kind == "STRUCTURE" and wanted == "DICT":
        return OPEN
    if kind != wanted:
        if kind in _WIDENS.get(wanted or "", frozenset()):
            return _choices_fit(source.choices, target.choices)
        return Fit("mismatch", f"a {_name(source)} is not a {_name(target)}")

    if kind in _IDENTIFIED:
        if (
            OPEN_IDENTIFIER in (source.identifier, target.identifier)
            or source.identifier == target.identifier
        ):
            return OK
        return Fit("mismatch", f"a {_name(source)} is not a {_name(target)}")
    if kind == "LIST":
        return _within(
            fits(source.children[0].port, target.children[0].port), "an item"
        )
    if kind == "DICT":
        return _dict_fits(source, target)
    if kind == "MODEL":
        return _fields_fit(source, target)
    return _choices_fit(source.choices, target.choices)


def _within(fit: Fit, where: str) -> Fit:
    return (
        Fit("mismatch", f"{where}: {fit.reason}") if fit.status == "mismatch" else fit
    )


def _dict_fits(source: CatalogPort, target: CatalogPort) -> Fit:
    item = target.child(ITEM_KEY)
    if item is not None:
        return _join(
            [
                _within(fits(child.port, item.port), child.key)
                for child in source.children
            ]
        )
    return _fields_fit(source, target)


def _fields_fit(source: CatalogPort, target: CatalogPort) -> Fit:
    results: list[Fit] = []
    for field in target.children:
        given = source.child(field.key) or source.child(ITEM_KEY)
        if given is None:
            if not field.port.nullable:
                results.append(Fit("mismatch", f"no field {field.key!r}"))
            continue
        results.append(_within(fits(given.port, field.port), field.key))
    return _join(results)


def binding_fits(
    local: CatalogPort, writes: CatalogPort, bind_kinds: Mapping[str, frozenset[str]]
) -> Fit:
    """Whether a local takes what a control writes (a BINDING's child), by ``bind_kinds``."""
    if local.is_open or writes.is_open:
        return OPEN
    if writes.kind == "UNION":
        # The control writes one of them, by how it is set (a ToggleGroup's ``multiple``).
        results = [
            binding_fits(local, child.port, bind_kinds) for child in writes.children
        ]
        if any(result.status == "ok" for result in results):
            return OK
        if any(result.status == "open" for result in results):
            return OPEN
        return Fit(
            "mismatch",
            f"a {_name(writes)} control writes none a {_name(local)} local takes",
        )
    allowed = bind_kinds.get(writes.kind or "")
    if allowed is None:
        return fits(writes, local)
    if local.kind not in allowed:
        return Fit(
            "mismatch",
            f"a {_name(writes)} control writes a {', '.join(sorted(allowed))} local, not a {_name(local)}",
        )
    if local.kind == writes.kind and local.kind in ("LIST", "DICT", "MODEL"):
        return fits(writes, local)
    return OK


def literal_port(value: object) -> CatalogPort:
    """The port of a literal: its kind, and itself as its one choice (so choices are checked)."""
    if value is None:
        return CatalogPort(kind=None, nullable=True)
    if isinstance(value, bool):
        return CatalogPort(kind="BOOL", choices=(Choice(value),))
    if isinstance(value, int):
        return CatalogPort(kind="INT", choices=(Choice(value),))
    if isinstance(value, float):
        return CatalogPort(kind="FLOAT", choices=(Choice(value),))
    if isinstance(value, str):
        return CatalogPort(kind="STRING", choices=(Choice(value),))
    return CatalogPort(kind=None)
