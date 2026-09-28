"""Utils for Rekuest"""

from typing import TYPE_CHECKING, Any

import inflection

if TYPE_CHECKING:
    from arkitekt_spec.declare.structures.registry import StructureRegistry


def is_local_var(type_: Any, structure_registry: "StructureRegistry | None" = None) -> bool:
    """Check if the type is a local variable (context or state).

    Local variables are injected by the agent, so they must not become ports on the
    definition. ``ReadOnly[SomeState]`` counts too: it is an ``Annotated`` wrapper, so
    ``is_state`` does not see through it, and without this a read-only state parameter
    was published as a required argument the caller could never supply.

    So is one annotated with a client a registered service returns (``mikro:
    Mikro``), which the agent supplies from the app it is bound to -- known only
    through ``structure_registry`` -- one annotated ``Task``, and one annotated with
    the class the app declared as its app context (handed in by ``run(context=...)``).
    """

    from arkitekt_spec.declare.agents.context import is_context
    from arkitekt_spec.declare.state.predicate import is_app_context, is_read_only_state, is_state
    from arkitekt_spec.declare.task import is_task

    return (
        is_context(type_, structure_registry)
        or is_app_context(type_, structure_registry)
        or is_state(type_, structure_registry)
        or is_read_only_state(type_, structure_registry)
        or (structure_registry is not None and structure_registry.is_client(type_))
        or is_task(type_)
    )


def interface_name(func: Any) -> str:
    """Infer an interface name from a function or class name (CamelCase → snake_case)."""
    return inflection.underscore(func.__name__)
