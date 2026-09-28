from typing import Annotated, TypeAlias, TypeVar

from arkitekt_spec.declare.protocol.types import AnyState

# 1. Define a TypeVar to represent the generic inner type
T = TypeVar("T", bound=AnyState)


# 2. Define your metadata marker
# This can be a simple class, string, or object
class ReadOnlyAnnotation:
    """Metadata marker for read-only fields."""



# 3. Create the generic TypeAlias
# The syntax ReadOnlyType[T] allows you to pass any type into it
ReadOnly: TypeAlias = Annotated[T, ReadOnlyAnnotation()]
