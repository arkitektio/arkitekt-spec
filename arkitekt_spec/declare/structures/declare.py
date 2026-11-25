"""A structure declares itself: ``@structure("@mikro/arraydataset")`` on its expander.

The expander says how to fetch one back and names the client it needs the way an
action does; what it returns is the class that travels, and that class is marked::

    @structure("@mikro/arraydataset")
    async def expand_array_dataset(id: str, mikro: Mikro) -> ArrayDataset:
        return await mikro.aget_array_dataset(id)

The client the expander asks for owns the structure: an app using ``Mikro`` takes
every structure it owns, and an annotation naming ``ArrayDataset`` brings ``Mikro``
along. Nothing is registered.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from arkitekt_spec.actions import AssignWidgetInput, ReturnWidgetInput
from arkitekt_spec.declare.blok.origin import DeclaredAt, declared_at
from arkitekt_spec.declare.client import ClientDeclaration, client_of
from arkitekt_spec.declare.markers import STRUCTURE, marker_of, stamp
from arkitekt_spec.declare.structures.errors import StructureDefinitionError
from arkitekt_spec.declare.structures.registry import (
    _travelling_class,
    injected_clients_of,
)
from arkitekt_spec.declare.structures.types import (
    Describer,
    Expander,
    ExpanderT,
    ManyExpander,
    Shrinker,
    is_valid_identifier,
)


@dataclass(eq=False)
class StructureDeclaration:
    """What ``@structure`` says about a class that travels by id."""

    identifier: str
    cls: type[object]
    aexpand: Expander
    ashrink: Shrinker | None = None
    aexpand_many: ManyExpander | None = None
    describe: Describer | None = None
    default_widget: AssignWidgetInput | None = None
    default_returnwidget: ReturnWidgetInput | None = None
    description: str | None = None
    owner: ClientDeclaration[Any] | None = None
    """The client its expander asks for; ``None`` for one that needs none."""
    declared_at: DeclaredAt | None = field(default=None, repr=False)


def _qualname(function: object) -> str:
    return f"{getattr(function, '__module__', '?')}.{getattr(function, '__qualname__', function)}"


def _same(old: StructureDeclaration, new: StructureDeclaration) -> bool:
    """The same declaration made again: its module imported a second time."""
    return old.identifier == new.identifier and _qualname(old.aexpand) == _qualname(
        new.aexpand
    )


def structure(
    identifier: str,
    *,
    cls: type[object] | None = None,
    client: type[Any] | None = None,
    widget: AssignWidgetInput | None = None,
    description: str | None = None,
    expand_many: ManyExpander | None = None,
    shrink: Shrinker | None = None,
    returnwidget: ReturnWidgetInput | None = None,
    describe: Describer | None = None,
) -> Callable[[ExpanderT], ExpanderT]:
    """Declare the class an expander returns as a structure travelling as ``identifier``.

    Args:
        identifier: What it travels as, ``@package/key``: a contract with every app.
        cls: The class that travels, when the expander's return annotation is not it.
        client: Which of the clients the expander asks for owns it, when it asks
            for several.
        widget: The widget a user picks one with.
        description: What it is, for the UI.
        expand_many: Fetches several ids at once.
        shrink: Turns the object into its id. Defaults to reading ``.id``.
        returnwidget: The widget one is shown with.
        describe: Computes an object's descriptors (``{key: value}``), cheaply and
            without asking a server.

    Returns:
        A decorator returning the expander unchanged.

    Raises:
        StructureDefinitionError: If the identifier is malformed, the class cannot
            be told, the expander asks for a class that is not a ``@client``, or
            for several without ``client=``.
    """
    at = declared_at()
    if not is_valid_identifier(identifier):
        raise StructureDefinitionError(
            f"'{identifier}' is not a structure identifier: the rekuest server only "
            "accepts '@package/key'."
        )

    def declare(expand: ExpanderT) -> ExpanderT:
        travelling = _travelling_class(expand, identifier, cls)
        injects = injected_clients_of(expand, identifier, "expand")
        if shrink is not None:
            injects.update(injected_clients_of(shrink, identifier, "shrink"))
        if expand_many is not None:
            injects.update(injected_clients_of(expand_many, identifier, "expand_many"))

        owners: list[ClientDeclaration[Any]] = []
        for key, wanted in sorted(injects.items()):
            declaration = client_of(wanted)
            if declaration is None:
                raise StructureDefinitionError(
                    f"'{identifier}': its expander wants a {wanted.__name__} as '{key}', "
                    "which is not a client. Declare it with @client on its class."
                )
            if declaration not in owners:
                owners.append(declaration)
        if client is not None:
            owner = client_of(client)
            if owner is None or owner not in owners:
                raise StructureDefinitionError(
                    f"'{identifier}': client={client!r} is not one of the clients its "
                    "expander asks for."
                )
        elif len(owners) > 1:
            raise StructureDefinitionError(
                f"'{identifier}': its expander asks for {[o.name for o in owners]}; say "
                "which owns it with client=."
            )
        else:
            owner = owners[0] if owners else None

        declaration = StructureDeclaration(
            identifier=identifier,
            cls=travelling,
            aexpand=expand,
            ashrink=shrink,
            aexpand_many=expand_many,
            describe=describe,
            default_widget=widget,
            default_returnwidget=returnwidget,
            description=description,
            owner=owner,
            declared_at=at,
        )
        stamp(travelling, STRUCTURE, declaration, same=_same)
        if owner is not None:
            owner.structures[:] = [
                s for s in owner.structures if s.identifier != identifier
            ] + [declaration]
        return expand

    return declare


def structure_of(cls: object) -> StructureDeclaration | None:
    """The structure declaration ``cls`` itself carries, or ``None``."""
    return marker_of(cls, STRUCTURE)


__all__ = ["StructureDeclaration", "structure", "structure_of"]
