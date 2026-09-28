"""Predicates telling what an annotation means to an app: a state, a read-only state, a context."""

from typing import TYPE_CHECKING, Any, TypeVar

from arkitekt_spec.declare.definition.define import get_args, is_annotated
from arkitekt_spec.declare.state.types import ReadOnlyAnnotation

if TYPE_CHECKING:
    from arkitekt_spec.declare.structures.registry import StructureRegistry

T = TypeVar("T")


def is_state(cls: Any, structure_registry: "StructureRegistry | None") -> bool:
    """Whether ``cls`` is a class the app registered as a state.

    Without a registry nothing is: being a state is a declaration on an app,
    not a property of the class.
    """
    return structure_registry is not None and structure_registry.is_state(cls)


def is_app_context(cls: Any, structure_registry: "StructureRegistry | None") -> bool:
    """Whether ``cls`` is a class the app declared as its app context.

    Without a registry nothing is: it is a declaration on an app (``@app.app_context``).
    """
    return structure_registry is not None and structure_registry.is_app_context(cls)


def is_read_only_state(cls: Any, structure_registry: "StructureRegistry | None") -> bool:
    """Whether ``cls`` is a state of the app marked read-only, i.e. ``ReadOnly[SomeState]``.

    The marker is a :class:`~rekuest.state.types.ReadOnlyAnnotation` *instance*
    carried in the ``Annotated`` extras, so it has to be matched with ``isinstance``.
    Comparing it against the ``ReadOnly`` type alias itself never matches, which silently
    classified such a parameter as neither writeable nor read-only -- so it was dropped
    from the actor's kwargs entirely and the call failed with a missing argument.
    """
    if not is_annotated(cls):
        return False
    real_type, *annotations = get_args(cls)
    if not is_state(real_type, structure_registry):
        return False
    return any(isinstance(a, ReadOnlyAnnotation) for a in annotations)


def get_read_only_state_type(cls: Any) -> type:
    """Unwrap ``ReadOnly[SomeState]`` to ``SomeState``."""
    real_type, *_ = get_args(cls)
    return real_type


def get_state_name(cls: Any, structure_registry: "StructureRegistry") -> str:
    """The interface the app registered the state class ``cls`` under."""
    return structure_registry.state_for(cls).interface


def get_state_locks(cls: Any, structure_registry: "StructureRegistry") -> list[str]:
    """The locks the app requires to change the state class ``cls``."""
    return list(structure_registry.state_for(cls).required_locks)
