"""Converters that turn plain Python classes into FullFilled types.

The structure registry calls these when a class is registered or derived:
enums (and ``Literal``) become FullFilledEnum, classes implementing the structure
protocol (get_identifier/ashrink/aexpand) become FullFilledStructure when
registered with ``register_from_protocol``, and classes registered as memory
structures become FullFilledMemoryStructure, kept in the local shelve. Nothing
is converted unasked: only enums are derived without being registered first.
"""

import re
from collections.abc import Callable
from enum import Enum
from typing import (
    Any,
    Literal,
    get_args,
    get_origin,
)

import inflection

from arkitekt_spec.actions import (
    ChoiceAssignWidgetInput,
    ChoiceInput,
    ChoiceReturnWidgetInput,
)
from arkitekt_spec.declare.structures.errors import StructureDefinitionError
from arkitekt_spec.declare.structures.types import (
    FullFilledEnum,
    FullFilledStructure,
)
from arkitekt_spec.declare.structures.utils import build_instance_predicate
from arkitekt_spec.scalars import Identifier, validate_identifier


def model_identifier(package: str, cls: type[Any]) -> str:
    """The identifier a model travels as when none is given: ``@package/snake_case_name``.

    ``package`` is whatever names the declaring side -- an app's identifier, or
    the class's module -- with everything the server refuses in that slot
    replaced by ``-``.
    """
    slot = re.sub(r"[^A-Za-z0-9_.-]+", "-", package).strip("-") or "app"
    return f"@{slot}/{inflection.underscore(cls.__name__)}"


def cls_to_identifier(cls: type[Any]) -> Identifier:
    """Derive an identifier string from a class's module and name.

    The server requires ``@package/key`` (``^@[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$``)
    and rejects an implementation whose structure ports carry anything else, so
    the module goes in the package slot and the class name in the key slot.
    Dots are legal in both slots, so this is a pure reshaping of the old
    ``module.name`` form and keeps identifiers exactly as unique as before.
    """
    try:
        return validate_identifier(f"@{cls.__module__.lower()}/{cls.__name__.lower()}")
    except AttributeError as e:
        raise StructureDefinitionError(
            f"Cannot convert {cls} to identifier. The class needs to have a"
            " __module__ and __name__ attribute."
        ) from e


def identity_default_converter(x: str) -> str:
    """Convert a value to its string representation."""
    return x


def make_enum_converter(cls: type[Enum]) -> Callable[[Any], str]:
    """Create a converter that maps a default value to its enum member name.

    Handles both enum instances and raw values (e.g. functools.partial treated
    as a descriptor in Python 3.13+ so it bypasses normal Enum member wrapping).
    """

    def converter(value: Any) -> str:
        if isinstance(value, cls):
            return value.name
        # Fallback: search registered members by .value first.
        for member in cls:
            if member.value is value or member.value == value:
                return member.name
        # Last resort: scan class attributes directly (Python 3.13+ descriptor
        # behaviour means partial-valued members never appear in __members__).
        for attr_name, attr_val in vars(cls).items():
            if attr_name.startswith("_"):
                continue
            if attr_val is value or attr_val == value:
                return attr_name
        raise ValueError(f"Cannot convert {value!r} to an enum name for {cls}")

    return converter


def enum_choices(cls: type[Enum]) -> list[ChoiceInput]:
    """Build the ChoiceInput list for an enum's members."""
    return [
        ChoiceInput(label=key, value=key, description=value.__doc__)
        for key, value in cls.__members__.items()
    ]


def is_global_structure(cls: type[Any]) -> bool:
    """Check whether a class implements the global structure protocol."""
    return (
        hasattr(cls, "get_identifier")
        and hasattr(cls, "aexpand")
        and hasattr(cls, "ashrink")
    )


def fullfilled_enum_from_cls(cls: type[Enum]) -> FullFilledEnum:
    """Build a FullFilledEnum from an Enum subclass."""
    choices = enum_choices(cls)

    return FullFilledEnum(
        cls=cls,
        identifier=cls_to_identifier(cls),
        choices=choices,
        members=dict(cls.__members__),
        predicate=build_instance_predicate(cls),
        description=cls.__doc__,
        convert_default=make_enum_converter(cls),
        default_widget=ChoiceAssignWidgetInput(),
        default_returnwidget=ChoiceReturnWidgetInput(),
    )


def is_literal(cls: Any) -> bool:
    """Check whether an annotation is a ``typing.Literal[...]``."""
    return get_origin(cls) is Literal


def _literal_identifier(values: tuple[Any, ...]) -> Identifier:
    """Derive a deterministic identifier for a literal-derived enum.

    Same literal members (in the same order) always produce the same
    identifier, so the same ``Literal[...]`` used across functions resolves to
    the same enum on the wire. The server takes ``@package/key`` only, so the
    package is ``literal`` and anything outside its alphabet becomes ``_``.
    """
    slug = "_".join(str(value).lower() for value in values)
    return validate_identifier(f"@literal/{re.sub(r'[^A-Za-z0-9_.-]', '_', slug)}")


def _is_literal_member(value: Any, values: tuple[Any, ...]) -> bool:
    """Whether ``value`` is one of the literal's members.

    Compared by type as well as equality, so ``True`` is not a member of
    ``Literal[1]`` and ``1`` is not a member of ``Literal[True]``.
    """
    return any(type(value) is type(member) and value == member for member in values)


def _literal_default_converter(values: tuple[Any, ...]) -> Callable[[Any], str]:
    """Create a converter that maps a literal default to its choice name."""

    def converter(value: Any) -> str:
        if not _is_literal_member(value, values):
            raise StructureDefinitionError(
                f"Default {value!r} is not one of the literal's members {values}"
            )
        return str(value)

    return converter


def fullfilled_enum_from_literal(cls: Any) -> FullFilledEnum:
    """Build a FullFilledEnum from a ``typing.Literal[...]`` annotation.

    The Literal stays a Literal. It is the enum's ``cls``, and its members are
    the literal values themselves, so a function annotated ``Literal["a", "b"]``
    is handed the bare ``"a"``. On the wire it is a rekuest enum whose choices
    are the stringified values; the same members in the same order share an
    identifier across functions.
    """
    values = get_args(cls)
    if not values:
        raise StructureDefinitionError(f"Literal {cls} has no members")

    members = {str(value): value for value in values}
    if len(members) != len(values):
        raise StructureDefinitionError(
            f"Literal {cls} has members that stringify alike: {values}"
        )

    return FullFilledEnum(
        cls=cls,
        identifier=_literal_identifier(values),
        choices=[ChoiceInput(label=key, value=key) for key in members],
        members=members,
        predicate=lambda value: _is_literal_member(value, values),
        description=None,
        convert_default=_literal_default_converter(values),
        default_widget=ChoiceAssignWidgetInput(),
        default_returnwidget=ChoiceReturnWidgetInput(),
    )


def fullfilled_structure_from_cls(cls: type[Any]) -> FullFilledStructure:
    """Build a FullFilledStructure from a class implementing the global
    structure protocol (get_identifier/ashrink/aexpand)."""
    if not hasattr(cls, "get_identifier"):
        raise StructureDefinitionError(
            f"Class {cls} does not have a get_identifier method"
        )

    return FullFilledStructure(
        cls=cls,
        identifier=cls.get_identifier(),
        aexpand=cls.aexpand,
        ashrink=cls.ashrink,
        predicate=getattr(cls, "predicate", None) or build_instance_predicate(cls),
        description=None,
        convert_default=getattr(cls, "convert_default", identity_default_converter),
        default_widget=cls.get_default_widget()
        if hasattr(cls, "get_default_widget")
        else None,
        default_returnwidget=cls.get_default_returnwidget()
        if hasattr(cls, "get_default_returnwidget")
        else None,
    )
