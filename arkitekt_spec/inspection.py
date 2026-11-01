"""What an app declares, as found by running ``arkitekt inspect all`` on its image."""

from pydantic import Field

from arkitekt_spec.actions import (
    AssetInput,
    BlokImplementationInput,
    ImplementationInput,
    LockImplementationInput,
    StateImplementationInput,
)
from arkitekt_spec.base import WireModel
from arkitekt_spec.manifest import Requirement


class InspectedAsset(AssetInput):
    """A file the app brings, as its image holds it: what the server is told, and where it is."""

    path: str | None = Field(
        default=None,
        description="Where the file is, relative to the app's directory, with forward slashes. Absent for a file the image does not hold: that one names a `source`.",
    )


class Inspection(WireModel):
    """The declaration an app image makes, plus the services it requires.

    The envelope reads across versions (unknown keys ignored, see
    :class:`~arkitekt_spec.base.WireModel`). The entries are the action language of
    :mod:`arkitekt_spec.actions`, which refuses unknown keys: a malformed definition
    fails where it is read rather than being stored half-understood.
    """

    spec_version: str = Field(
        description="The arkitekt-spec the image wrote this with, which may be older than the "
        "file carrying it.",
    )
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
    assets: list[InspectedAsset] = Field(
        default_factory=list,
        description="The files the app brings with it, by the name its bloks call them.",
    )
