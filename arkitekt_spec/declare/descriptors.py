"""Testing a value's descriptors against what a port requires or provides.

A port states constraints (``Requires`` on an argument, ``Provides`` on a
return) over descriptor keys, and a structure may say how an object's
descriptors are computed (``describe``). This module is the comparison of the
two, with the server's meaning of every operator: what passes here is what the
server's own matching lets through.

A constraint on a key the object's descriptors do not hold is **not tested**.
Such a key is provenance (a label mask is told from an image by whoever made
it, not by its shape), and only the producer can state it. Whether the key
exists at all is the server's to say: it refuses a port constraining a key no
service declares.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from enum import Enum
from typing import Any

from arkitekt_spec.actions import ProvidesInput, RequiresInput

Constraint = RequiresInput | ProvidesInput


def operator_name(operator: object) -> str:
    """A constraint's operator as its plain name.

    ``use_enum_values=True`` means the field may hold the enum or its value.
    """
    return str(operator.value) if isinstance(operator, Enum) else str(operator)


def holds(operator: object, actual: object, expected: object) -> bool:
    """Whether a present descriptor value satisfies one operator.

    Args:
        operator: The ``DescriptorOperator``, or its name.
        actual: The object's value for the key.
        expected: The constraint's value.

    Returns:
        Whether the constraint holds.

    Raises:
        TypeError: If the operator cannot be applied to these values (ordering a
            string, membership without a list).
        ValueError: If the operator is unknown.
    """
    name = operator_name(operator)
    if name == "EXISTS":
        # No value, or True, asks for presence; False asks for absence.
        return expected is not False
    if name in ("EQUALS", "MATCHES"):
        # MATCHES is equality on the server, so it is equality here.
        return bool(actual == expected)
    if name == "NOT_EQUALS":
        return bool(actual != expected)
    if name in ("GTE", "LTE"):
        if not _is_number(actual) or not _is_number(expected):
            raise TypeError(f"{name} orders numbers; {actual!r} and {expected!r} are not both numbers")
        return bool(actual >= expected if name == "GTE" else actual <= expected)  # type: ignore[operator]
    if name in ("IN", "NOT_IN"):
        if isinstance(expected, str) or not isinstance(expected, Sequence):
            raise TypeError(f"{name} needs a list of allowed values, got {expected!r}")
        found = actual in expected
        return found if name == "IN" else not found
    if name == "CONTAINS":
        if isinstance(actual, str) or not isinstance(actual, Sequence):
            raise TypeError(f"CONTAINS needs a list descriptor, got {actual!r}")
        return any(item == expected for item in actual)
    raise ValueError(f"Unknown descriptor operator {name!r}")


def unfulfilled(constraints: Iterable[Constraint], descriptors: Mapping[str, Any]) -> tuple[str, ...]:
    """Every constraint these descriptors do not satisfy, one line each.

    A constraint on a key the descriptors do not hold is skipped: it cannot be
    tested from the object.

    Args:
        constraints: A port's ``requires`` or ``provides``.
        descriptors: The object's descriptors, as its structure computes them.

    Returns:
        The failures, empty when everything testable holds.
    """
    failures: list[str] = []
    for constraint in constraints:
        if constraint.key not in descriptors:
            continue
        actual = descriptors[constraint.key]
        if not holds(constraint.operator, actual, constraint.value):
            failures.append(
                f"{constraint.key} {operator_name(constraint.operator)} {constraint.value!r} (actual {actual!r})"
            )
    return tuple(failures)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
