from typing import Any, Self

from pydantic import BaseModel, model_validator

from arkitekt_spec.rules import check_demo_state


class CreateBlokInputTrait(BaseModel):
    """Validate a standalone blok's components and demo state against its dependencies.

    A blok an *agent* implements is checked with the rest of its declaration
    (:func:`rekuest.definition.checks.check_agent_input`); ``CreateBlokInput`` is
    not part of the action language, so it keeps this trait.
    """

    @model_validator(mode="after")  # type: ignore[override]
    def validate_components_and_demo_state(self) -> Self:
        """Validate blok components against the provided dependencies."""
        from arkitekt_spec.declare.blok.validate import validate_blok

        model: Any = self
        for component in model.components or ():
            validate_blok(component, list(model.dependencies or ()))
        check_demo_state(model.dependencies, model.demo_state)
        return self
