from typing import Self, TypeVar

from pydantic import BaseModel, model_validator

from arkitekt_spec.actions import (
    AgentDependencyInput,
    BlokLocalInput,
    BlokQueryInput,
    BlokSubscriptionInput,
    ComponentNodeInput,
)

Spec = TypeVar("Spec", bound=BaseModel)


def _as(model: type[Spec], values: object) -> tuple[Spec, ...]:
    """Generated client inputs (models or dicts) as the spec's models, through their dumps."""
    items: list[object] = list(values) if isinstance(values, (list, tuple)) else []  # pyright: ignore[reportUnknownArgumentType]
    return tuple(model.model_validate(item.model_dump() if isinstance(item, BaseModel) else item) for item in items)


class CreateBlokInputTrait(BaseModel):
    """Validate a custom blok's tree against what it declares (blok language v2, kind ``custom``).

    A blok an *agent* implements is checked with the rest of its declaration
    (:func:`arkitekt_spec.declare.definition.checks.check_agent_input`); ``CreateBlokInput`` is
    not part of the action language, so it keeps this trait.

    The tree is checked against its scope resolved
    (:func:`~arkitekt_spec.declare.blok.infer.resolve_scope`): every local without a kind takes
    one from the tree. Every ``deps.<key>`` it reads must be declared (``deps.unknown``). The
    input itself is sent as written; the server resolves it the same way and stores both.
    """

    @model_validator(mode="after")  # type: ignore[override]
    def validate_components(self) -> Self:
        """Refuse the blok on the first rule of the language its tree breaks: its components, or
        its BSX ``source`` parsed against the Orkestrator's view."""
        from arkitekt_spec.blok.bsx import parse_bsx
        from arkitekt_spec.blok.catalogs import default_view
        from arkitekt_spec.declare.blok.validate import BlokScope, validate_blok

        model: object = self
        context: object = getattr(model, "context", None)
        scope = BlokScope(
            kind="custom",
            context=str(getattr(context, "value", context) or "HTML"),
            dependencies=_as(AgentDependencyInput, getattr(model, "dependencies", None)),
            locals=_as(BlokLocalInput, getattr(model, "locals", None)),
            queries=_as(BlokQueryInput, getattr(model, "queries", None)),
            subscriptions=_as(BlokSubscriptionInput, getattr(model, "subscriptions", None)),
        )
        trees = list(_as(ComponentNodeInput, getattr(model, "components", None)))
        source: object = getattr(model, "source", None)
        if isinstance(source, str):
            trees.append(parse_bsx(source, view=default_view()))
        for tree in trees:
            validate_blok(tree, scope)
        return self
