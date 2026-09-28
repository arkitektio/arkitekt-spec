"""Utility functions for Rekuest structures."""

from .types import Predicator


async def id_shrink(
    value: object,
) -> str:
    """Identity shrink function.

    This function does not change the value and is used as a default shrink function for structures.

    Args:
        value (object): The value to be shrunk.

    Returns:
        object: The shrunk value.
    """
    if hasattr(value, "id"):
        return value.id
    else:
        raise ValueError(f"Value {value} does not have an id attribute. Cannot shrink.")


def build_instance_predicate(cls: type[object]) -> Predicator:
    """Build a predicate function that checks if an object is an instance of the given class."""
    return lambda value: isinstance(value, cls)

