"""Models: dataclasses that travel by value, field by field.

A model is declared on an app (``@app.model``), which makes the class a
dataclass if it is not one and registers it on that app's structures under an
identifier. Nothing is written on the class. :func:`model_field` is the field
helper that carries a description, a label and validators to the port.
"""

import inspect
import re
from collections.abc import Callable
from dataclasses import MISSING, dataclass, field
from typing import Any, TypeVar, get_type_hints

from fieldz import Field, fields  # type: ignore
from pydantic import BaseModel

from arkitekt_spec.actions import ValidatorInput

T = TypeVar("T", bound=type[Any])


def model_field(
    *,
    default: Any = MISSING,
    default_factory: Callable[[], Any] | Any = MISSING,
    init: bool = True,
    repr: bool = True,
    compare: bool = True,
    kw_only: bool | Any = MISSING,
    description: str | None = None,
    validators: list[ValidatorInput] | None = None,
    label: str | None = None,
) -> Any:  # a dataclass field, typed as the value it stands in for
    """A model field that carries a description, a label and validators to the port.

    The dataclass field of a model (``@app.model``), and the field specifier type
    checkers see for it: ``default``/``default_factory``/``init``/``kw_only`` mean what
    they mean to :func:`dataclasses.field`.

    Args:
        default: The field's default value.
        default_factory: Builds the default, for mutable defaults.
        init: Whether the field is a constructor parameter.
        repr: Whether the field appears in the model's repr.
        compare: Whether the field takes part in equality.
        kw_only: Whether the field is keyword-only in the constructor.
        description: Human-readable description used in generated definitions.
        validators: Validators the port applies to the field.
        label: Display label for UI rendering.

    Returns:
        The dataclass field.

    Examples:
        Define a model field with UI metadata::

            threshold: float = model_field(default=0.5, description="Confidence cutoff")
    """
    return field(  # type: ignore[call-overload]
        default=default,
        default_factory=default_factory,
        init=init,
        repr=repr,
        compare=compare,
        kw_only=kw_only,
        metadata={"description": description, "validators": validators, "label": label},
    )


def ensure_model_dataclass(cls: T) -> T:
    """Make ``cls`` a dataclass if it is not one, for use as a model.

    Called by :meth:`~rekuest.structures.registry.StructureRegistry.model` when
    an app declares the class. Dataclass construction failures are augmented
    with source-aware error messages that point at the offending class line
    when possible.

    Args:
        cls: The class an app declares as a model.

    Returns:
        The same class, a dataclass.

    Raises:
        TypeError: If dataclass conversion fails. The raised error includes file
            and line context when source code is available.
    """

    try:
        # Check if it's already valid (e.g. manually decorated with @dataclass)
        fields(cls)
    except TypeError:
        try:
            # If not, attempt to transform it into a dataclass
            return ensure_model_dataclass(dataclass(cls))  # type: ignore
        except Exception as e:
            # --- Enhanced Error Reporting ---
            try:
                # 1. Get source lines and file path
                lines, start_line = inspect.getsourcelines(cls)
                file_path = inspect.getfile(cls)

                # 2. Heuristic: Find the line causing the error
                error_line_no = start_line
                raw_line = lines[0]  # Default to class definition line

                # Look for field names in the error message (e.g., 't_hooks')
                match = re.search(r"'([^']*)'", str(e))
                if match:
                    field_name = match.group(1)
                    # Scan source for that field name
                    for idx, line in enumerate(lines):
                        if re.search(r"\b" + re.escape(field_name) + r"\b", line):
                            error_line_no = start_line + idx
                            raw_line = line
                            break

                # 3. Create the visual pointer (^^^^^)
                stripped_line = raw_line.lstrip()
                indentation = len(raw_line) - len(stripped_line)
                pointer = " " * indentation + "^" * len(stripped_line.strip())

                # 4. Construct the error message
                error_msg = (
                    f"Model error in '{cls.__name__}':\n"
                    f'  File "{file_path}", line {error_line_no}\n'
                    f"{raw_line.rstrip()}\n"
                    f"{pointer}\n"
                    f"TypeError: {e}\n"
                )
            except (OSError, TypeError):
                # Fallback if source is not available
                error_msg = f"Model definition error in '{cls.__name__}': {e}"

            raise TypeError(error_msg) from None

    return cls


class InspectedModel(BaseModel):
    """A model that can be used to serialize and deserialize"""

    identifier: str
    description: str | None
    args: list["InspectedArg"]


class InspectedArg(BaseModel):
    """A fullfiled argument of a model that can be used to serialize and deserialize"""

    key: str
    default: Any | None
    cls: Any
    label: str | None
    description: str | None
    validators: list[ValidatorInput] | None


def inspect_args_for_model(cls: type[Any]) -> list[InspectedArg]:
    """Retrieve the arguments for a model."""
    children_classes: tuple[Field[Any], ...] = fields(cls)  # type: ignore

    try:
        resolved_hints = get_type_hints(cls, include_extras=True)
    except Exception:
        resolved_hints = {}

    args: list[InspectedArg] = []
    for yfield in children_classes:
        type_ = resolved_hints.get(yfield.name) or yfield.annotated_type or yfield.type
        args.append(
            InspectedArg(
                cls=type_,
                default=yfield.default if yfield.default != Field.MISSING else None,
                key=yfield.name,
                description=yfield.description
                or yfield.metadata.get("description", None),
                validators=yfield.metadata.get("validators", None),
                label=yfield.metadata.get("label", None),
            )
        )
    return args


def inspect_model_class(cls: type[Any], identifier: str) -> InspectedModel:
    """The model class ``cls`` as a port is built from it: its fields, under ``identifier``."""
    return InspectedModel(
        identifier=identifier,
        description=cls.__doc__,
        args=inspect_args_for_model(cls),
    )
