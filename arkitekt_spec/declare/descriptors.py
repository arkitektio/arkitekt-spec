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

import math
from collections.abc import Iterable, Mapping, Sequence
from enum import Enum
from typing import TYPE_CHECKING, Any

from arkitekt_spec.actions import ProvidesInput, RequiresInput
from arkitekt_spec.rules import iter_call_arguments

if TYPE_CHECKING:
    from arkitekt_spec.declare.task import StateRequirement

Constraint = RequiresInput | ProvidesInput

Number = int | float


def _hardware_value(hardware: Mapping[str, Any], name: str) -> Any:
    """One of ``AGENT_HARDWARE`` from what an agent says: the GPU values are derived."""
    if name == "gpu_count":
        return len(hardware["gpus"]) if "gpus" in hardware else None
    if name == "gpu_memory_mib":
        memories = [gpu["memory_mib"] for gpu in hardware.get("gpus", ()) if gpu.get("memory_mib") is not None]
        return max(memories) if memories else None
    return hardware.get(name)


def _fits_read(path: str, descriptors: Mapping[str, Any], hardware: Mapping[str, Any]) -> Number | None:
    root, name = path.split("/", 1)
    value = (
        descriptors.get(name.replace("~1", "/").replace("~0", "~"))
        if root == "value"
        else _hardware_value(hardware, name)
    )
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _fits_number(argument: Any, descriptors: Mapping[str, Any], hardware: Mapping[str, Any]) -> Number | None:
    if argument.value_path is not None:
        return _fits_read(argument.value_path, descriptors, hardware)
    if argument.util_call is None:
        return argument.value_literal
    operands: dict[str, Number] = {}
    for nested in argument.util_call.arguments:
        operand = _fits_number(nested, descriptors, hardware)
        if operand is None:
            return None
        operands[nested.key] = operand
    match argument.util_call.operation:
        case "math.add":
            return operands["a"] + operands["b"]
        case "math.sub":
            return operands["a"] - operands["b"]
        case "math.mul":
            return operands["a"] * operands["b"]
        case "math.div":
            return operands["a"] / operands["b"] if operands["b"] != 0 else None
        case "math.neg":
            return -operands["a"]
        case "math.abs":
            return abs(operands["value"])
        case "math.floor":
            return math.floor(operands["value"])
        case "math.ceil":
            return math.ceil(operands["value"])
    return None


def fits_holds(call: Any, descriptors: Mapping[str, Any], hardware: Mapping[str, Any]) -> bool:
    """Whether an object fits, the way the database predicate that matches actions answers it.

    The first evaluator of a call tree in Python, and only of the subset a ``Fits`` may use
    (:func:`arkitekt_spec.rules.check_fits`). A comparison on a missing value is false.
    """
    arguments = {argument.key: argument for argument in call.arguments or ()}
    match call.operation:
        case "logic.all":
            return all(fits_holds(item.util_call, descriptors, hardware) for item in arguments["values"].value_list)
        case "logic.any":
            return any(fits_holds(item.util_call, descriptors, hardware) for item in arguments["values"].value_list)
    a = _fits_number(arguments["a"], descriptors, hardware)
    b = _fits_number(arguments["b"], descriptors, hardware)
    if a is None or b is None:
        return False
    match call.operation:
        case "cmp.gt":
            return a > b
        case "cmp.gte":
            return a >= b
        case "cmp.lt":
            return a < b
        case "cmp.lte":
            return a <= b
    return False


def fits_can_be_tested(call: Any, descriptors: Mapping[str, Any], hardware: Mapping[str, Any]) -> bool:
    """Whether everything a formula reads is known here.

    An agent describes an object from what it fetched, and may not know its own memory. Like a
    constraint on a key the object's descriptors do not hold, that is not tested.
    """
    return all(
        _fits_read(argument.value_path, descriptors, hardware) is not None
        for argument in iter_call_arguments(call)
        if argument.value_path is not None
    )


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


_MISSING = object()


def resolve_pointer(document: Any, pointer: str) -> Any:
    """The value at an RFC 6901 pointer, or ``_MISSING`` when the document has none.

    ``""`` is the whole document; ``/a/0`` is key ``a``, then index 0. ``~1`` and ``~0``
    stand for ``/`` and ``~`` in a key.
    """
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise ValueError(f"{pointer!r} is not a JSON pointer: it must start with '/'")
    current = document
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if token not in current:
                return _MISSING
            current = current[token]
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
            if not token.isdigit() or (len(token) > 1 and token.startswith("0")):
                return _MISSING
            index = int(token)
            if index >= len(current):
                return _MISSING
            current = current[index]
        else:
            return _MISSING
    return current


def unmet_state(requirements: Iterable[StateRequirement], states: Mapping[str, Any]) -> tuple[str, ...]:
    """Every state requirement these states do not satisfy, one line each.

    Unlike :func:`unfulfilled`, nothing is skipped: a state the agent does not have, or
    a path its value does not hold, is a failure (except ``EXISTS`` with
    ``value=False``, which asks for absence). An operator that cannot compare the
    values found (ordering a string) is a failure too, never an exception.

    Args:
        requirements: A call's ``state_requires``.
        states: The agent's states by interface, each as its JSON value.

    Returns:
        The failures, empty when every requirement holds.
    """
    failures: list[str] = []
    for requirement in requirements:
        name = operator_name(requirement.operator)
        where = f"{requirement.state}{requirement.path}"
        if requirement.state not in states:
            failures.append(f"{where}: the agent has no state {requirement.state!r}")
            continue
        actual = resolve_pointer(states[requirement.state], requirement.path)
        if actual is _MISSING:
            if name == "EXISTS" and requirement.value is False:
                continue
            failures.append(f"{where} {name} {requirement.value!r} (missing)")
            continue
        try:
            ok = holds(requirement.operator, actual, requirement.value)
        except (TypeError, ValueError) as error:
            failures.append(f"{where} {name} {requirement.value!r}: {error}")
            continue
        if not ok:
            failures.append(f"{where} {name} {requirement.value!r} (actual {actual!r})")
    return tuple(failures)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
