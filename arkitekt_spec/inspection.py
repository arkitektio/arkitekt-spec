"""What an app declares, as found by running ``arkitekt inspect all`` on its image."""

from typing import Any

from pydantic import Field

from arkitekt_spec.base import WireModel
from arkitekt_spec.manifest import Requirement

#: One entry of rekuest's action language, carried verbatim.
ActionLanguageEntry = dict[str, Any]


class Inspection(WireModel):
    """The declaration an app image makes, plus the services it requires.

    The *envelope* is this spec's. The entries of ``implementations``, ``states``,
    ``locks`` and ``bloks`` are rekuest's action language (an ``ImplementationInput``,
    ``StateImplementationInput``, ``LockImplementationInput`` and
    ``BlokImplementationInput`` each) and are carried here as JSON: the producer builds
    them with ``rekuest.protocol`` and every consumer validates them with its own
    rekuest models. That keeps this package free of rekuest while leaving exactly one
    owner per layer.
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
    implementations: list[ActionLanguageEntry] = Field(
        default_factory=list, description="rekuest `ImplementationInput`s."
    )
    states: list[ActionLanguageEntry] = Field(
        default_factory=list, description="rekuest `StateImplementationInput`s."
    )
    locks: list[ActionLanguageEntry] = Field(
        default_factory=list, description="rekuest `LockImplementationInput`s."
    )
    bloks: list[ActionLanguageEntry] = Field(
        default_factory=list, description="rekuest `BlokImplementationInput`s."
    )
