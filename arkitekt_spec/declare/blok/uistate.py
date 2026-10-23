"""The types of a blok's own UI state: what ``local_state`` declares and what its reads write.

Every other path a blok reads has a type: a dependency's state, a bound call's task, a
``foreach`` item. Its own UI state gets one here, three ways:

- **a typed class** -- ``local_state={"form": Form}`` (a dataclass or pydantic model whose
  fields all have defaults) or an instance of one: the fields are the type, the defaults or the
  instance the initial value;
- **its values** -- ``local_state={"form": {"iterations": 15}}``: a plain value is typed by what
  it holds (a ``None``, an empty list's item, stay untyped);
- **a read** -- ``<query bind="cells" …>``: the root holds a resource record, its ``data`` as
  the query answers it (see :mod:`arkitekt_spec.declare.blok.graphql`).
"""

import dataclasses
import enum
import types
import typing
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from arkitekt_spec.actions import ComponentNodeInput, PortKind, PortMatchInput
from arkitekt_spec.declare.blok.graphql import (
    ITEM,
    QUERY_BIND_PROP,
    QUERY_COMPONENT,
    QUERY_DOCUMENT_PROP,
    QUERY_SCHEMA_PROP,
)
from arkitekt_spec.declare.blok.walk import FOREACH_COMPONENT, node_context

StructureMatch = Callable[[Any, str], PortMatchInput | None]
"""How a type the UI state cannot describe itself (a structure: ``Image``) becomes a match."""


# --------------------------------------------------------------------------- values


def infer_match(key: str, value: Any) -> PortMatchInput | None:
    """A JSON value's type, as far as the value says it; ``None`` when it says nothing."""
    if value is None:
        return None
    if isinstance(value, bool):
        return PortMatchInput(key=key, kind=PortKind.BOOL)
    if isinstance(value, int):
        return PortMatchInput(key=key, kind=PortKind.INT)
    if isinstance(value, float):
        return PortMatchInput(key=key, kind=PortKind.FLOAT)
    if isinstance(value, str):
        return PortMatchInput(key=key, kind=PortKind.STRING)
    if isinstance(value, (list, tuple)):
        item = infer_match(ITEM, value[0]) if value else None
        return PortMatchInput(key=key, kind=PortKind.LIST, children=(item or PortMatchInput(key=ITEM),))
    if isinstance(value, Mapping):
        children = tuple(infer_match(str(name), inner) or PortMatchInput(key=str(name)) for name, inner in value.items())
        return PortMatchInput(key=key, kind=PortKind.DICT, children=children)
    return None


# --------------------------------------------------------------------------- classes


def is_state_class(value: Any) -> bool:
    """A dataclass or pydantic model, as a class or as an instance."""
    cls = value if isinstance(value, type) else type(value)
    return dataclasses.is_dataclass(cls) or (isinstance(cls, type) and issubclass(cls, BaseModel))


def initial_value(value: Any, root: str) -> Any:
    """What a typed root starts as: the instance's values, or the class built with its defaults."""
    if isinstance(value, type):
        try:
            value = value()
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"local_state {root!r}: every field of {value.__name__} needs a default to start from, "
                f"or pass an instance ({error})"
            ) from None
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return _jsonable(dataclasses.asdict(value))


def _jsonable(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(inner) for inner in value]
    return value


def class_match(key: str, cls: Any, structure: StructureMatch | None = None) -> PortMatchInput | None:
    """A type hint as a match: scalars, lists, string-keyed dicts, optionals and nested classes."""
    origin = typing.get_origin(cls)
    args = typing.get_args(cls)
    if origin in (typing.Union, types.UnionType):
        inner = [arg for arg in args if arg is not type(None)]
        if len(inner) == 1:
            match = class_match(key, inner[0], structure)
            return None if match is None else match.model_copy(update={"nullable": True})
        return None
    if origin is typing.Annotated:
        return class_match(key, args[0], structure)
    if origin is typing.Literal:
        return PortMatchInput(key=key, kind=PortKind.STRING if all(isinstance(a, str) for a in args) else None)
    if origin in (list, tuple, set, frozenset):
        item = class_match(ITEM, args[0], structure) if args else None
        return PortMatchInput(key=key, kind=PortKind.LIST, children=(item or PortMatchInput(key=ITEM),))
    if origin in (dict, Mapping):
        item = class_match(ITEM, args[1], structure) if len(args) == 2 else None
        return PortMatchInput(key=key, kind=PortKind.DICT, children=(item or PortMatchInput(key=ITEM),))
    if cls is bool:
        return PortMatchInput(key=key, kind=PortKind.BOOL)
    if cls is int:
        return PortMatchInput(key=key, kind=PortKind.INT)
    if cls is float:
        return PortMatchInput(key=key, kind=PortKind.FLOAT)
    if cls is str:
        return PortMatchInput(key=key, kind=PortKind.STRING)
    if isinstance(cls, type) and issubclass(cls, enum.Enum):
        return PortMatchInput(key=key, kind=PortKind.STRING)
    if isinstance(cls, type) and is_state_class(cls):
        hints = typing.get_type_hints(cls, include_extras=True)
        names = [f.name for f in dataclasses.fields(cls)] if dataclasses.is_dataclass(cls) else list(cls.model_fields)
        return PortMatchInput(
            key=key,
            kind=PortKind.DICT,
            children=tuple(class_match(name, hints[name], structure) or PortMatchInput(key=name) for name in names),
        )
    if cls is Any:
        return None
    return structure(cls, key) if structure is not None else None


def local_state_matches(
    local_state: Mapping[str, Any] | None, structure: StructureMatch | None = None
) -> tuple[dict[str, Any], dict[str, PortMatchInput | None]]:
    """``local_state`` as the initial values the wire carries, and the type of each root."""
    values: dict[str, Any] = {}
    matches: dict[str, PortMatchInput | None] = {}
    for root, declared in (local_state or {}).items():
        if is_state_class(declared):
            values[root] = initial_value(declared, root)
            matches[root] = class_match(root, declared if isinstance(declared, type) else type(declared), structure)
        else:
            values[root] = declared
            matches[root] = infer_match(root, declared)
    return values, matches


# --------------------------------------------------------------------------- reads


@dataclass(frozen=True)
class QueryRead:
    """One ``query`` in a tree: where it writes, and what it asks."""

    node_id: str
    context: str
    bind: str | None
    schema: str | None
    document: str | None


def query_reads(component: ComponentNodeInput) -> list[QueryRead]:
    """Every ``query`` in the tree, refusing two on one path and one repeated by a loop."""
    reads: list[QueryRead] = []
    seen: dict[str, str] = {}

    def visit(node: ComponentNodeInput, repeated: bool) -> None:
        if node.component == QUERY_COMPONENT:
            props = {prop.key: prop.static_value for prop in node.props or ()}
            bind = props.get(QUERY_BIND_PROP)
            bind = ".".join(part for part in bind.replace("/", ".").split(".") if part) if isinstance(bind, str) else None
            context = node_context(node)
            if bind is not None:
                if repeated:
                    raise ValueError(
                        f"{context}: a query inside a {FOREACH_COMPONENT} binds '{bind}' once for every item; "
                        "bind it outside the loop"
                    )
                if bind in seen:
                    raise ValueError(f"{context}: query '{seen[bind]}' already writes '{bind}'; two reads would race")
                seen[bind] = node.id
            schema = props.get(QUERY_SCHEMA_PROP)
            document = props.get(QUERY_DOCUMENT_PROP)
            reads.append(
                QueryRead(
                    node_id=node.id,
                    context=context,
                    bind=bind,
                    schema=schema if isinstance(schema, str) else None,
                    document=document if isinstance(document, str) else None,
                )
            )
        for child in node.children or ():
            visit(child, repeated or node.component == FOREACH_COMPONENT)

    visit(component, False)
    return reads
