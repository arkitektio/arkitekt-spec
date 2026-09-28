"""What an app declares, as found by running ``arkitekt inspect all`` on its image."""

from pydantic import Field

from arkitekt_spec.actions import (
    BlokImplementationInput,
    ImplementationInput,
    LockImplementationInput,
    StateImplementationInput,
)
from arkitekt_spec.base import WireModel
from arkitekt_spec.manifest import Requirement


class Inspection(WireModel):
    """The declaration an app image makes, plus the services it requires.

    The envelope reads across versions (unknown keys ignored, see
    :class:`~arkitekt_spec.base.WireModel`). The entries are the action language of
    :mod:`arkitekt_spec.actions`, which refuses unknown keys: a malformed definition
    fails where it is read rather than being stored half-understood.
    """

    size: int | None = Field(
        default=None, description="The uncompressed size of the image, in bytes."
    )
    description: str | None = Field(
        default=None, description="What the app is, as its agent declares it."
    )
    requirements: list[Requirement] = Field(
        default_factory=list, description="The services the app needs."
    )
    implementations: list[ImplementationInput] = Field(default_factory=list)
    states: list[StateImplementationInput] = Field(default_factory=list)
    locks: list[LockImplementationInput] = Field(default_factory=list)
    bloks: list[BlokImplementationInput] = Field(default_factory=list)
