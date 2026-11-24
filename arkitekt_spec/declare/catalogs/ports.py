"""The port a catalog types a prop, an operation argument or its result by.

The same port an action's argument has (kind, identifier, nullable, choices, children, ...),
plus three kinds only a catalog may use: ``CALLBACK`` (its children are the payload it is
called with), ``ASSET`` (a file an app registered) and ``BINDING`` (a path to a local the
control reads and writes; its one child, keyed ``...``, is the value). A child adds its
``key`` and ``description`` and may be keyed ``value``.

A port whose ``kind`` is None is *open*: it takes any value. Only what the spec owns is
open (the language's and run's props, generic operations, record fields); a component a
renderer draws types every prop.

Plain dataclasses: this module imports nothing of the package (``catalogs`` is reached while
the action models are still being built).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

PORT_KINDS = (
    "INT",
    "STRING",
    "STRUCTURE",
    "LIST",
    "BOOL",
    "DICT",
    "FLOAT",
    "DATE",
    "UNION",
    "ENUM",
    "MODEL",
    "MEMORY_STRUCTURE",
    "INTERFACE",
    "QUANTITY",
    "CALLBACK",
    "ASSET",
    "BINDING",
)
"""Every port kind, as ``PortKind`` spells them (hard-coded: this module imports nothing)."""

CATALOG_ONLY_KINDS = frozenset({"CALLBACK", "ASSET", "BINDING"})
"""Kinds a catalog types a prop by and no agent declares: a UI's callbacks, files and bindings."""

CALLBACK = "CALLBACK"
ASSET = "ASSET"
BINDING = "BINDING"
ITEM_KEY = "..."
OPEN_IDENTIFIER = "*"
"""A STRUCTURE identifier a catalog uses for a prop that takes any structure."""

_IDENTIFIED = frozenset({"STRUCTURE", "MEMORY_STRUCTURE", "INTERFACE"})
_LEAVES = frozenset(
    {
        "INT",
        "STRING",
        "BOOL",
        "FLOAT",
        "DATE",
        "ENUM",
        "QUANTITY",
        "ASSET",
        *_IDENTIFIED,
    }
)


@dataclass(frozen=True)
class Choice:
    """One value a port takes, and how it is shown."""

    value: object
    label: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class CatalogPort:
    """A typed value: a kind and what the kind needs. ``kind`` None is open (any value)."""

    kind: str | None
    identifier: str | None = None
    nullable: bool = False
    choices: tuple[Choice, ...] | None = None
    children: tuple["CatalogChildPort", ...] = ()
    default: object = None
    reference_unit: str | None = None
    proposed_units: tuple[str, ...] | None = None
    dimension: str | None = None

    @property
    def is_open(self) -> bool:
        return self.kind is None

    def child(self, key: str) -> "CatalogChildPort | None":
        return next((child for child in self.children if child.key == key), None)


@dataclass(frozen=True)
class CatalogChildPort:
    """A port below another: a list's item (``...``), a field, a variant, a payload field."""

    key: str
    port: CatalogPort
    description: str | None = None


OPEN = CatalogPort(kind=None)


def _get(raw: Mapping[str, object], snake: str, camel: bool) -> object:
    head, *rest = snake.split("_")
    return raw.get(head + "".join(part.title() for part in rest) if camel else snake)


def parse_port(raw: object, where: str, *, camel: bool = False) -> CatalogPort:
    """A port as a catalog writes one; ``None`` is open.

    Raises:
        ValueError: If the kind is unknown or the port lacks what its kind needs.
    """
    if raw is None:
        return OPEN
    if not isinstance(raw, Mapping):
        raise ValueError(f"{where}: a port is an object, not {raw!r}")
    kind = raw.get("kind")
    if kind is not None and kind not in PORT_KINDS:
        raise ValueError(f"{where}: unknown port kind {kind!r}")
    children = tuple(
        _parse_child(child, f"{where}.{index}", camel=camel)
        for index, child in enumerate(raw.get("children") or ())  # type: ignore[arg-type]
    )
    choices = raw.get("choices")
    port = CatalogPort(
        kind=kind,  # type: ignore[arg-type]
        identifier=raw.get("identifier") or None,  # type: ignore[arg-type]
        nullable=bool(raw.get("nullable") or False),
        choices=tuple(_parse_choice(choice, where) for choice in choices)  # type: ignore[union-attr]
        if isinstance(choices, list)
        else None,
        children=children,
        default=raw.get("default"),
        reference_unit=_get(raw, "reference_unit", camel) or None,  # type: ignore[arg-type]
        proposed_units=tuple(_get(raw, "proposed_units", camel) or ()) or None,  # type: ignore[arg-type]
        dimension=raw.get("dimension") or None,  # type: ignore[arg-type]
    )
    check_shape(port, where)
    return port


def _parse_child(raw: object, where: str, *, camel: bool) -> CatalogChildPort:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("key"), str):
        raise ValueError(f"{where}: a child port has a key")
    key = str(raw["key"])
    return CatalogChildPort(
        key=key,
        port=parse_port(raw, f"{where}({key})", camel=camel),
        description=raw.get("description"),  # type: ignore[arg-type]
    )


def _parse_choice(raw: object, where: str) -> Choice:
    if isinstance(raw, Mapping):
        return Choice(
            value=raw.get("value"),
            label=raw.get("label"),
            description=raw.get("description"),
        )  # type: ignore[arg-type]
    if isinstance(raw, (str, int, float, bool)):
        return Choice(value=raw)
    raise ValueError(f"{where}: a choice is a value or {{value, label}}, not {raw!r}")


def check_shape(port: CatalogPort, where: str) -> None:
    """What a kind needs: children, an identifier, choices.

    Raises:
        ValueError: If the port lacks one, or carries one its kind does not take.
    """
    kind = port.kind
    count = len(port.children)
    if kind is None:
        return
    if kind in _LEAVES and count:
        raise ValueError(f"{where}: a {kind} port has no children")
    if kind in _IDENTIFIED and not port.identifier:
        raise ValueError(f"{where}: a {kind} port names its identifier")
    if kind == "ENUM" and not port.choices:
        raise ValueError(f"{where}: an ENUM port lists its choices")
    if kind == "LIST" and count != 1:
        raise ValueError(f"{where}: a LIST port has exactly one child, its item")
    if kind in ("DICT", "MODEL") and count == 0:
        raise ValueError(f"{where}: a {kind} port has children")
    if kind == "UNION" and count < 2:
        raise ValueError(f"{where}: a UNION port has at least two variants")
    if kind == BINDING and (count != 1 or port.children[0].key != ITEM_KEY):
        raise ValueError(
            f"{where}: a BINDING port has one child, keyed '...': the value it writes"
        )
    keys = [child.key for child in port.children]
    if kind != "UNION" and len(set(keys)) != len(keys):
        raise ValueError(f"{where}: child keys repeat")


def port_json(port: CatalogPort, *, camel: bool = False) -> dict[str, object] | None:
    """The port as a catalog writes it (``None`` for an open root), fields left out when empty."""
    if port.is_open and not port.children:
        return None
    return _port_fields(port, camel=camel)


def _port_fields(port: CatalogPort, *, camel: bool) -> dict[str, object]:
    def name(snake: str) -> str:
        head, *rest = snake.split("_")
        return head + "".join(part.title() for part in rest) if camel else snake

    out: dict[str, object] = {"kind": port.kind}
    if port.identifier:
        out["identifier"] = port.identifier
    if port.nullable:
        out["nullable"] = True
    if port.choices is not None:
        out["choices"] = [
            {
                k: v
                for k, v in (
                    ("value", c.value),
                    ("label", c.label),
                    ("description", c.description),
                )
                if v is not None
            }
            for c in port.choices
        ]
    if port.children:
        out["children"] = [
            {
                "key": child.key,
                **_port_fields(child.port, camel=camel),
                **({"description": child.description} if child.description else {}),
            }
            for child in port.children
        ]
    if port.default is not None:
        out["default"] = port.default
    if port.reference_unit:
        out[name("reference_unit")] = port.reference_unit
    if port.proposed_units:
        out[name("proposed_units")] = list(port.proposed_units)
    if port.dimension:
        out["dimension"] = port.dimension
    return out


def kinds_in(port: CatalogPort) -> set[str | None]:
    """Every kind in the port's tree, ``None`` for an open node."""
    found: set[str | None] = {port.kind}
    for child in port.children:
        found |= kinds_in(child.port)
    return found


def summary(port: CatalogPort) -> str:
    """The port as one line: ``LIST<STRING>``, ``UNION<STRING | INT>``, ``CALLBACK(value: BOOL)``."""
    kind = port.kind or "ANY"
    nullable = "?" if port.nullable else ""
    if port.choices and kind in ("STRING", "INT", "FLOAT", "ENUM"):
        return " | ".join(repr(choice.value) for choice in port.choices) + nullable
    if kind in _IDENTIFIED:
        return f"{kind}({port.identifier}){nullable}"
    if kind == "UNION":
        return " | ".join(summary(child.port) for child in port.children) + nullable
    if kind in ("LIST", BINDING):
        return f"{kind}<{summary(port.children[0].port)}>{nullable}"
    if kind in ("DICT", "MODEL", CALLBACK):
        inner = ", ".join(
            summary(child.port)
            if child.key == ITEM_KEY
            else f"{child.key}: {summary(child.port)}"
            for child in port.children
        )
        brackets = (
            ("<", ">")
            if kind == "DICT" and port.children and port.children[0].key == ITEM_KEY
            else ("(", ")")
        )
        return f"{kind}{brackets[0]}{inner}{brackets[1]}{nullable}"
    return kind + nullable


def scalar(
    kind: str, *, nullable: bool = False, choices: Sequence[object] | None = None
) -> CatalogPort:
    """A leaf port of ``kind``, optionally with choices."""
    return CatalogPort(
        kind=kind,
        nullable=nullable,
        choices=tuple(Choice(value=value) for value in choices)
        if choices is not None
        else None,
    )
