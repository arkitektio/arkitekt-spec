"""The scalars of the action language, as plain pydantic types.

These are the wire shapes only. Where rekuest's own scalar does more -- parsing a
search query with graphql-core, opening a file for a media upload -- that behaviour
stays in rekuest, which validates on top of these types; the spec depends on neither.
"""

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
