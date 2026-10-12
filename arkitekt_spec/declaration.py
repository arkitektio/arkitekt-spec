"""An app

An app is declared in code once, and that declaration leaves the process in several
shapes, each read by a different party:

- the **login manifest** (fakts): who the app is and which services it needs;
- the **agent registration** (rekuest, distributed mode): what it offers;
- the **inspection** of a built image (kabinet): all of the above plus the image size;
- the **API schema** of a served app (server mode): what its actions take and return.

:class:`AppDeclaration` is that declaration as data. Each shape is a projection of it,
so none of them can say something the others do not.
"""

from typing import Any

from pydantic import Field

from arkitekt_spec.actions import (
    AssetInput,
    BlokImplementationInput,
    ImplementAgentInput,
    ImplementationInput,
    LockImplementationInput,
    StateImplementationInput,
)
from arkitekt_spec.base import WireModel
from arkitekt_spec.inspection import InspectedAsset, Inspection
from arkitekt_spec.manifest import AppManifest, Requirement


class AppDeclaration(WireModel):
    """Everything an app is, independent of where or how it runs."""

    manifest: AppManifest
    requirements: list[Requirement] = Field(
        default_factory=list, description="The services the app needs."
    )
    implementations: list[ImplementationInput] = Field(default_factory=list)
    states: list[StateImplementationInput] = Field(default_factory=list)
    locks: list[LockImplementationInput] = Field(default_factory=list)
    bloks: list[BlokImplementationInput] = Field(default_factory=list)
    assets: list[InspectedAsset] = Field(default_factory=list)

    def to_inspection(self, size: int | None = None) -> Inspection:
        """The declaration as a built image records it (``deployments.yaml``)."""
        return Inspection(
            size=size,
            description=self.manifest.description,
            requirements=list(self.requirements),
            implementations=list(self.implementations),
            states=list(self.states),
            locks=list(self.locks),
            bloks=list(self.bloks),
            assets=list(self.assets),
        )

    def to_agent_input(self, name: str | None = None) -> ImplementAgentInput:
        """The declaration as an agent registers it (the action-language payload).

        ``description`` is only set when the app has one, so an undescribed app
        leaves the field unset rather than sending an explicit null.
        """
        # Set only when there is something to say: an explicit null is not "unset".
        optional: dict[str, Any] = {}
        if self.manifest.description is not None:
            optional["description"] = self.manifest.description
        if self.assets:
            # Without the paths, which are this machine's.
            optional["assets"] = tuple(
                AssetInput(name=a.name, kind=a.kind, ref=a.ref, size=a.size) for a in self.assets
            )
        return ImplementAgentInput(
            name=name,
            implementations=tuple(self.implementations),
            states=tuple(self.states),
            locks=tuple(self.locks),
            bloks=tuple(self.bloks),
            **optional,
        )
