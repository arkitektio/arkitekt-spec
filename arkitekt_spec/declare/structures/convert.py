"""Converters that turn plain Python classes into FullFilled types.

The structure registry calls these when a class is registered or derived:
enums (and ``Literal``) become FullFilledEnum, classes implementing the structure
protocol (get_identifier/ashrink/aexpand) become FullFilledStructure when
registered with ``register_from_protocol``, and classes registered as memory
structures become FullFilledMemoryStructure, kept in the local shelve. Nothing
is converted unasked: only enums are derived without being registered first.
"""

import re
import sys
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Literal,
    get_args,
    get_origin,
)

import inflection
from pydantic import BaseModel, ConfigDict

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


def package_slot(package: str) -> str:
    """``package`` as the package slot of ``@package/key``: everything the server refuses
    there replaced by ``-``."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", package).strip("-") or "app"


def model_identifier(package: str, cls: type[Any]) -> str:
    """The identifier a model travels as when none is given: ``@package/snake_case_name``.

    ``package`` names the declaring side: the app's identifier, or the class's
    module for a registry no app owns (a service package's).
    """
    return f"@{package_slot(package)}/{inflection.underscore(cls.__name__)}"


INSTALLED_DIRECTORIES = frozenset({"site-packages", "dist-packages"})
"""Directories an installer puts other distributions in: never an app's own source."""


def source_root(constructed_in: Path) -> Path | None:
    """The directory an app's own source lives in, given the file that constructs the app.

    The top-level package the file is in -- climbing while a directory has an
    ``__init__.py`` -- or, for a plain script, its directory. ``None`` for code
    that has no file (typed into a console).
    """
    if not constructed_in.is_file():
        return None
    root = constructed_in.resolve().parent
    while root.parent != root and (root.parent / "__init__.py").is_file():
        root = root.parent
    return root


class AppNamespace(BaseModel):
    """The app a registry belongs to, and what counts as the app's own source.

    A class the app defines itself derives its identifier in the app's namespace
    (``@<identifier>/<name>``), so it is the same however the app is launched: a
    class in a script is ``__main__.Method`` when the script is run and
    ``script.Method`` when it is imported. A class from anywhere else -- an
    installed library, another package -- keeps its module-derived identifier, so
    a library type is the same in every app that uses it.

    A class is the app's own when (see :meth:`defines`):

    - its module is ``__main__`` or ``module``, the module that constructed the app; or
    - its module's file is under ``root`` (the top-level package or script directory
      of that module, see :func:`source_root`) and not inside a ``site-packages`` or
      ``dist-packages`` directory below it, which holds installed distributions
      (a virtualenv kept in the app's directory).

    The rule reads files, not module names, so it gives the same answer whether
    the app is run as a script, with ``-m`` or imported.
    """

    model_config = ConfigDict(frozen=True)

    identifier: str
    """The app's identifier: the namespace of what it defines."""
    root: Path | None = None
    """The directory of the app's own source; ``None`` when it has no file."""
    module: str | None = None
    """The name of the module that constructed the app (``__main__`` when run)."""

    def defines(self, cls: type[Any]) -> bool:
        """Whether ``cls`` is a class this app defines itself (see the class docstring)."""
        module_name = cls.__module__
        if module_name == "__main__" or (self.module is not None and module_name == self.module):
            return True
        if self.root is None:
            return False
        module = sys.modules.get(module_name)
        if module is None:
            return False
        try:
            file = module.__file__
        except AttributeError:  # a builtin module has none
            return False
        if file is None:
            return False
        try:
            relative = Path(file).resolve().relative_to(self.root)
        except ValueError:
            return False
        return INSTALLED_DIRECTORIES.isdisjoint(relative.parts)

    def of(self, cls: type[Any]) -> str | None:
        """The namespace ``cls`` derives its identifier in: the app's when it defines it, else none."""
        return self.identifier if self.defines(cls) else None


def cls_to_identifier(cls: type[Any], namespace: str | None = None) -> Identifier:
    """Derive an identifier for an enum or a memory structure: ``@<namespace>/<classname>``.

    ``namespace`` is the identifier of the app that defines the class (see
    :class:`AppNamespace`), so the same app gets the same identifier however it is
    launched. A class the app does not define itself -- a library's -- and one in
    a registry no app owns pass none and fall back to the class's module.

    The server requires ``@package/key`` (``^@[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$``),
    so the namespace goes in the package slot and the class name in the key slot.
    """
    try:
        package = package_slot(namespace) if namespace is not None else cls.__module__.lower()
        return validate_identifier(f"@{package}/{cls.__name__.lower()}")
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


def fullfilled_enum_from_cls(cls: type[Enum], namespace: str | None = None) -> FullFilledEnum:
    """Build a FullFilledEnum from an Enum subclass, its identifier in ``namespace`` (see :func:`cls_to_identifier`)."""
    choices = enum_choices(cls)

    return FullFilledEnum(
        cls=cls,
        identifier=cls_to_identifier(cls, namespace),
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
