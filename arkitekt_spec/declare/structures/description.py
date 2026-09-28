"""A structure, described as data.

A description says everything needed to let a type cross a node boundary, and
does nothing: building one registers nothing, anywhere. Whoever owns a registry
turns descriptions into registrations (:meth:`StructureRegistry.declare`), which
is what lets an app's structures be exactly those of its services instead of
whatever happened to be imported into the process.

A description carries no expand or shrink function. The service that declares it
builds a client per run, and that client expands and shrinks its structures by
identifier (:class:`~rekuest.structures.client.StructureClient`).
"""

from dataclasses import dataclass

from arkitekt_spec.actions import AssignWidgetInput


@dataclass(frozen=True)
class StructureDescription:
    """What it takes to send ``cls`` over the wire by id.

    Attributes:
        cls: The class that travels.
        identifier: The name it travels under, ``@package/key``. A contract
            between services, and what the service's client expands by.
        default_widget: The widget a user picks one with, if any.
        description: What it is, for the UI.
    """

    cls: type
    identifier: str
    default_widget: AssignWidgetInput | None = None
    description: str | None = None


__all__ = ["StructureDescription"]
