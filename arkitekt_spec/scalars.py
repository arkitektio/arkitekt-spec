"""The scalars of the action language, as plain pydantic types.

These are the wire shapes only. Where rekuest's own scalar does more -- parsing a
search query with graphql-core, opening a file for a media upload -- that behaviour
stays in rekuest, which validates on top of these types; the spec depends on neither.
"""

import re
from typing import Annotated, Any

from pydantic import AfterValidator, BeforeValidator


def _coerce_id(value: Any) -> Any:  # noqa: ANN401 -- a before-validator sees anything
    """Accept an int, or an object carrying an ``id``, where an ID string is expected."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value)
    identifier = getattr(value, "id", None)
    if not isinstance(value, str) and identifier is not None:
        return str(identifier)
    return value


def _check_identifier(value: str) -> str:
    """A global identifier reads ``@package/module``."""
    if "@" in value and "/" not in value:
        raise ValueError(
            "Identifier must follow '@package/module' when naming a global structure"
        )
    return value


ID = Annotated[str, BeforeValidator(_coerce_id)]
"""An object id. Ints and objects with an ``id`` are coerced to it."""

Identifier = Annotated[str, AfterValidator(_check_identifier)]
"""The identifier of a structure, e.g. ``@mikro/image``."""

SearchQuery = str
"""A GraphQL query a search widget runs. Parsed by rekuest and the server, not here."""

ActionHash = str
"""The hash of an action definition; see :func:`arkitekt_spec.actions.definition_hash`."""

Args = dict[str, Any]
"""Arguments, keyed by port."""

JSONSerializable = str | int | float | bool | None | dict[str, Any] | list[Any]
"""Any JSON value."""

MediaLike = str
"""A reference to uploaded media (a store key or URL)."""


def coerce_id(value: Any) -> str:  # noqa: ANN401 -- ints and objects with an id, too
    """An object id as a string: a str, an int, or anything carrying an ``id``."""
    coerced = _coerce_id(value)
    if not isinstance(coerced, str):
        raise TypeError(f"Cannot use {value!r} as an ID: expected a str, an int, or an object with an id")
    return coerced


def validate_identifier(value: str) -> str:
    """Check a structure identifier (``@package/key``) outside a pydantic field."""
    return _check_identifier(value)


# --- Search queries ---------------------------------------------------------
#
# A SEARCH widget's query is GraphQL. Its full shape is checked with graphql-core
# when that is installed (rekuest brings it); without it, the variables are read off
# the operation header, which is all the declaration-time rule ("every variable is
# backed by a filter port or a dependency") needs.

#: Variables the ward supplies at runtime, never backed by a filter or dependency.
RESERVED_SEARCH_VARIABLES = frozenset({"search", "values", "limit", "offset"})

_HEADER_VARIABLE = re.compile(r"\$([_A-Za-z][_0-9A-Za-z]*)\s*:")


def get_search_query_variables(query: str) -> list[str]:
    """The variable names a search query declares, in order."""
    try:
        from graphql import OperationDefinitionNode, parse
    except ImportError:
        header = query.split("{", 1)[0]
        return _HEADER_VARIABLE.findall(header)
    document = parse(query)
    definition = document.definitions[0]
    if not isinstance(definition, OperationDefinitionNode):
        return []
    return [v.variable.name.value for v in definition.variable_definitions or ()]


def validate_search_query(query: str) -> str:
    """Check a search query's shape, and return it normalised.

    One ``query`` operation whose first two variables are ``$search`` and ``$values``,
    selecting a field aliased ``options`` that selects ``value`` and ``label``. The
    shape is checked with graphql-core when it is installed; otherwise only the
    variable header is, and the query is returned as written.
    """
    try:
        from graphql import (
            FieldNode,
            GraphQLSyntaxError,
            OperationDefinitionNode,
            OperationType,
            parse,
            print_ast,
        )
    except ImportError:
        variables = get_search_query_variables(query)
        if variables[:2] != ["search", "values"]:
            raise ValueError(
                "A search query's first two variables must be $search and $values"
            ) from None
        return query

    try:
        document = parse(query)
    except GraphQLSyntaxError as e:
        raise ValueError(f"Could not parse the search query:\n{e}\n{query}") from e
    printed = print_ast(document)
    if len(document.definitions) != 1:
        raise ValueError("Only one definition allowed")
    definition = document.definitions[0]
    if not isinstance(definition, OperationDefinitionNode):
        raise ValueError("Needs an operation")  # noqa: TRY004 -- a validation error, as callers expect
    if definition.operation != OperationType.QUERY:
        raise ValueError("Needs to be a query operation")
    variables = definition.variable_definitions or ()
    if len(variables) < 2:
        raise ValueError(
            "At least two arguments should be provided "
            f"($search: String, $values: [ID]): Was given: {printed}"
        )
    if variables[0].variable.name.value != "search" or variables[0].type.kind != "named_type":
        raise ValueError(
            f"First parameter of a search query should be '$search: String': Was given: {printed}"
        )
    if variables[1].variable.name.value != "values":
        raise ValueError(
            f"Second parameter of a search query should be '$values: [ID]': Was given: {printed}"
        )
    wrapped = definition.selection_set.selections[0]
    if not isinstance(wrapped, FieldNode):
        raise ValueError(f"Wrapped query should be a field node: Was given: {printed}")  # noqa: TRY004
    if (wrapped.alias.value if wrapped.alias else wrapped.name.value) != "options":
        raise ValueError(f"First element of query should be 'options': Was given: {printed}")
    if not wrapped.selection_set:
        raise ValueError(f"Wrapped query should contain a selection set: Was given: {printed}")
    aliases = [
        field.alias.value if field.alias else field.name.value
        for field in wrapped.selection_set.selections
        if isinstance(field, FieldNode)
    ]
    if "value" not in aliases:
        raise ValueError("A search query needs a 'value' field: the selected value")
    if "label" not in aliases:
        raise ValueError("A search query needs a 'label' field: what the user sees")
    return printed
