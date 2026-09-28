"""Naming the positional arguments of a base-catalog operation.

The call walkers and purity checks this module used to hold are the spec's now
(:mod:`arkitekt_spec.rules`); what stays is the part that needs rekuest's base
function catalog.
"""

from collections.abc import Iterable
from typing import TYPE_CHECKING

from arkitekt_spec.declare.catalogs import base_operation

if TYPE_CHECKING:
    from arkitekt_spec.actions import ActionArgumentInput


def _is_positional(argument: "ActionArgumentInput") -> bool:
    return argument.key is None or argument.key.isdigit()


def resolve_base_arguments(
    operation: str, arguments: Iterable["ActionArgumentInput"], owner: str
) -> list["ActionArgumentInput"]:
    """Name the positional arguments of a base operation.

    Positional entries (index keys ``"0"``, ``"1"``, ... or no key) are mapped onto the
    base operation's parameters in order; keyword entries are checked against them; the
    result is in manifest order. Operations the base catalog does not know are returned
    unchanged (their positional entries keep their index keys for the UI to map).

    Raises:
        ValueError: too many positionals, an unknown keyword, a parameter given twice, or
            a required parameter missing.
    """
    spec = base_operation(operation)
    arguments = list(arguments)
    if spec is None:
        return arguments

    keys = spec.keys
    positional = sorted(
        (a for a in arguments if _is_positional(a)),
        key=lambda a: int(a.key) if a.key is not None else -1,
    )
    named = [a for a in arguments if not _is_positional(a)]

    if len(positional) > len(keys):
        raise ValueError(
            f"{owner}: {operation} takes at most {len(keys)} positional arguments ({', '.join(keys)})"
        )

    resolved: dict[str, ActionArgumentInput] = {}
    for index, argument in enumerate(positional):
        resolved[keys[index]] = argument.model_copy(update={"key": keys[index]})
    for argument in named:
        assert argument.key is not None
        if argument.key not in keys:
            raise ValueError(
                f"{owner}: {operation} does not accept argument {argument.key!r} (accepts {', '.join(keys)})"
            )
        if argument.key in resolved:
            raise ValueError(f"{owner}: {operation} got multiple values for {argument.key!r}")
        resolved[argument.key] = argument

    missing = [key for key in spec.required_keys if key not in resolved]
    if missing:
        raise ValueError(f"{owner}: {operation} requires arguments {missing}")

    return [resolved[key] for key in keys if key in resolved]
