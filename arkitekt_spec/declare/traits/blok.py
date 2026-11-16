from typing import Self, TypeVar

from pydantic import BaseModel, model_validator

from arkitekt_spec.actions import (
    AgentDependencyInput,
    ComponentNodeInput,
)

Spec = TypeVar("Spec", bound=BaseModel)


def _as(model: type[Spec], values: object) -> tuple[Spec, ...]:
    """Generated client inputs (models or dicts) as the spec's models, through their dumps."""
    items: list[object] = list(values) if isinstance(values, (list, tuple)) else []  # pyright: ignore[reportUnknownArgumentType]
    return tuple(model.model_validate(item.model_dump() if isinstance(item, BaseModel) else item) for item in items)


class CreateBlokInputTrait(BaseModel):
    """Validate a custom blok's tree, mounted on the dependencies it declares (blok language v2,
    a ``<Blok kind="custom">``).

    A blok an *agent* implements is checked with the rest of its declaration
    (:func:`arkitekt_spec.declare.definition.checks.check_agent_input`); ``CreateBlokInput`` is
    not part of the action language, so it keeps this trait.

    Every ``deps.<key>`` it reads must be declared (``deps.unknown``). The input itself is sent as
    written; the server resolves its locals the same way.
    """

    @model_validator(mode="after")  # type: ignore[override]
    def validate_components(self) -> Self:
        """Refuse the blok on the first rule of the language its tree breaks: its components, or
        its BSX ``source`` parsed against the base catalog's view."""
        from arkitekt_spec.blok.bsx import parse_bsx
        from arkitekt_spec.blok.catalogs import default_view
        from arkitekt_spec.declare.blok.validate import BlokScope, validate_blok

        model: object = self
        scope = BlokScope(
            kinds=("custom",),
            mount="createBlok",
            dependencies=_as(AgentDependencyInput, getattr(model, "dependencies", None)),
        )
        trees = list(_as(ComponentNodeInput, getattr(model, "components", None)))
        source: object = getattr(model, "source", None)
        if isinstance(source, str):
            trees.append(parse_bsx(source, view=default_view()))
        for tree in trees:
            validate_blok(tree, scope)
        return self
