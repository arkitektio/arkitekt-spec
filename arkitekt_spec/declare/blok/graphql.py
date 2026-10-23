"""A blok's GraphQL reads, checked against the schema of the service they run on.

A base ``query`` component names its service (``schema="mikro"``) and carries one GraphQL
document. When the app's registry has that service's SDL (``@registry.service(schema=...)``),
the document is validated against it and its selection set becomes the shape of the record's
``data``, so ``@cells.data.datasets`` is a path the validator can follow like any state.

graphql-core is optional for the spec: without it a query is not checked, and the caller says
so once. Nothing here imports it at module level.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from arkitekt_spec.actions import PortKind, PortMatchInput

QUERY_COMPONENT = "query"
QUERY_SCHEMA_PROP = "schema"
QUERY_DOCUMENT_PROP = "query"
QUERY_VARIABLES_PROP = "variables"
QUERY_BIND_PROP = "bind"

ITEM = "..."


def graphql_available() -> bool:
    """Whether graphql-core is installed, so queries can be checked at all."""
    try:
        import graphql  # noqa: F401
    except ImportError:
        return False
    return True


@dataclass(frozen=True)
class QueryVariable:
    """A variable a query declares: its GraphQL type as written, and whether it is required."""

    name: str
    type: str
    required: bool


@dataclass(frozen=True)
class QueryShape:
    """What a checked query is: its problems, the shape of its answer and its variables."""

    errors: tuple[str, ...] = ()
    data: PortMatchInput | None = None
    variables: tuple[QueryVariable, ...] = field(default=())


def read_only_problem(document: str) -> str | None:
    """Why a document may not run in a blok, or ``None``: exactly one ``query`` operation.

    The client's ``readOnlyQueryProblem``: a mutation or subscription is refused, whatever it
    would do, because a change goes through an action.
    """
    from graphql import GraphQLSyntaxError, OperationDefinitionNode, OperationType, parse

    try:
        definitions = parse(document).definitions
    except GraphQLSyntaxError as error:
        return f"the query is not valid GraphQL: {error.message}"
    operations = [d for d in definitions if isinstance(d, OperationDefinitionNode)]
    if any(operation.operation != OperationType.QUERY for operation in operations):
        return "only queries can run in a blok: a change goes through an action, never a mutation"
    if len(operations) != 1:
        return "the document must hold exactly one query"
    return None


def _build(sdl: str) -> Any:
    """The schema a service serves, taken as valid: a federation subgraph's SDL names directives
    (``@key``, ``@shareable``) it does not declare, which a strict build would refuse."""
    from graphql import build_schema

    return build_schema(sdl, assume_valid_sdl=True)


def schema_shape(sdl: str) -> dict[str, Any]:
    """The schema as plain data both sides compute alike: every named type, its fields with
    their arguments, members and values, by name, each type as GraphQL writes it (``[ID!]!``).

    Descriptions, directives and defaults are left out: they change no answer a blok reads.
    The client builds the same with graphql-js (``core/modules/hooks/serviceSchema.ts``); the
    printed SDL is no common ground, the two printers differ.
    """
    from graphql import (
        GraphQLEnumType,
        GraphQLInputObjectType,
        GraphQLInterfaceType,
        GraphQLObjectType,
        GraphQLUnionType,
    )

    schema = _build(sdl)
    types: dict[str, Any] = {}
    for name, named in schema.type_map.items():
        if name.startswith("__"):
            continue
        entry: dict[str, Any] = {"kind": _kind(named)}
        if isinstance(named, (GraphQLObjectType, GraphQLInterfaceType)):
            entry["fields"] = {
                field_name: {"type": str(f.type), "args": {arg: str(a.type) for arg, a in sorted(f.args.items())}}
                for field_name, f in sorted(named.fields.items())
            }
            entry["interfaces"] = sorted(i.name for i in named.interfaces)
        elif isinstance(named, GraphQLInputObjectType):
            entry["fields"] = {field_name: str(f.type) for field_name, f in sorted(named.fields.items())}
        elif isinstance(named, GraphQLUnionType):
            entry["types"] = sorted(t.name for t in named.types)
        elif isinstance(named, GraphQLEnumType):
            entry["values"] = sorted(named.values)
        types[name] = entry
    return types


def _kind(named: Any) -> str:
    from graphql import (
        GraphQLEnumType,
        GraphQLInputObjectType,
        GraphQLInterfaceType,
        GraphQLObjectType,
        GraphQLScalarType,
        GraphQLUnionType,
    )

    kinds = (
        (GraphQLObjectType, "OBJECT"),
        (GraphQLInterfaceType, "INTERFACE"),
        (GraphQLUnionType, "UNION"),
        (GraphQLEnumType, "ENUM"),
        (GraphQLInputObjectType, "INPUT_OBJECT"),
        (GraphQLScalarType, "SCALAR"),
    )
    return next(kind for cls, kind in kinds if isinstance(named, cls))


def schema_hash(sdl: str) -> str:
    """``sha256:<hex>`` of :func:`schema_shape` as compact, key-sorted JSON."""
    canonical = json.dumps(schema_shape(sdl), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def check_query(sdl: str, document: str) -> QueryShape:
    """Validate ``document`` against ``sdl`` and read the shape of its answer."""
    from graphql import (
        FragmentDefinitionNode,
        GraphQLSyntaxError,
        OperationDefinitionNode,
        parse,
        print_ast,
        validate,
    )

    problem = read_only_problem(document)
    if problem is not None:
        return QueryShape(errors=(problem,))
    schema = _build(sdl)
    try:
        ast = parse(document)
    except GraphQLSyntaxError as error:  # read_only_problem parsed it already; kept for safety
        return QueryShape(errors=(f"the query is not valid GraphQL: {error.message}",))
    errors = validate(schema, ast)
    if errors:
        return QueryShape(errors=tuple(error.message for error in errors))

    operation = next(d for d in ast.definitions if isinstance(d, OperationDefinitionNode))
    fragments = {d.name.value: d for d in ast.definitions if isinstance(d, FragmentDefinitionNode)}
    query_type = schema.query_type
    assert query_type is not None  # validate() refuses a query on a schema without one
    variables = tuple(
        QueryVariable(
            name=definition.variable.name.value,
            type=print_ast(definition.type),
            required=definition.type.kind == "non_null_type" and definition.default_value is None,
        )
        for definition in operation.variable_definitions or ()
    )
    data = PortMatchInput(
        key="data",
        kind=PortKind.DICT,
        nullable=True,
        children=_selection_children(operation.selection_set, query_type, fragments, schema),
    )
    return QueryShape(data=data, variables=variables)


# --------------------------------------------------------------------------- selection -> shape

_SCALAR_KINDS: Mapping[str, PortKind] = {
    "Int": PortKind.INT,
    "Float": PortKind.FLOAT,
    "String": PortKind.STRING,
    "ID": PortKind.STRING,
    "Boolean": PortKind.BOOL,
}


def _selection_children(selection_set: Any, parent: Any, fragments: Mapping[str, Any], schema: Any) -> tuple[PortMatchInput, ...]:
    """The fields a selection set answers with, by response key; fragments merged in."""
    from graphql import FieldNode, FragmentSpreadNode, InlineFragmentNode

    children: dict[str, PortMatchInput] = {}
    for selection in selection_set.selections:
        if isinstance(selection, FieldNode):
            name = selection.name.value
            key = selection.alias.value if selection.alias else name
            if name == "__typename":
                children[key] = PortMatchInput(key=key, kind=PortKind.STRING, nullable=False)
                continue
            definition = parent.fields[name]
            children[key] = _field_match(key, definition.type, selection, fragments, schema)
        else:
            if isinstance(selection, FragmentSpreadNode):
                fragment = fragments[selection.name.value]
            else:
                assert isinstance(selection, InlineFragmentNode)
                fragment = selection
            condition = fragment.type_condition
            narrowed = schema.get_type(condition.name.value) if condition is not None else parent
            for child in _selection_children(fragment.selection_set, narrowed, fragments, schema):
                # A field only one variant selects may be absent on the others.
                assert child.key is not None
                if child.key not in children:
                    children[child.key] = child if narrowed is parent else child.model_copy(update={"nullable": True})
    return tuple(children.values())


def _field_match(key: str, type_: Any, node: Any, fragments: Mapping[str, Any], schema: Any) -> PortMatchInput:
    """One field's answer as a match: lists hold their item under ``...``, objects their selection."""
    from graphql import GraphQLEnumType, GraphQLList, GraphQLNonNull, GraphQLScalarType

    nullable = True
    if isinstance(type_, GraphQLNonNull):
        nullable, type_ = False, type_.of_type
    if isinstance(type_, GraphQLList):
        item = _field_match(ITEM, type_.of_type, node, fragments, schema)
        return PortMatchInput(key=key, kind=PortKind.LIST, nullable=nullable, children=(item,))
    if isinstance(type_, GraphQLScalarType):
        # A custom scalar (a JSON, a date) can be read, not looked into.
        return PortMatchInput(key=key, kind=_SCALAR_KINDS.get(type_.name), nullable=nullable)
    if isinstance(type_, GraphQLEnumType):
        return PortMatchInput(key=key, kind=PortKind.STRING, nullable=nullable)
    return PortMatchInput(
        key=key,
        kind=PortKind.DICT,
        nullable=nullable,
        children=_selection_children(node.selection_set, type_, fragments, schema),
    )
