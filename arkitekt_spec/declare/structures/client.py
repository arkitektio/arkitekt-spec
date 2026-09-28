"""What a service's client does for rekuest: expand and shrink its structures.

A structure a service declares is only data (class, identifier, widget). Turning an
id back into an object, and an object into its id, is the job of the client that
service builds for a run -- the thing that holds the connection. rekuest never
calls a per-structure function for them: it asks the bound client, by identifier.

Clients usually get it from rath's ``ExpandsStructures`` (``rath.expansion``) --
rath, because every client package depends on it while rekuest is optional for
them -- by naming their expanders::

    class Mikro(Composition, MikroApi, ExpandsStructures):
        EXPANDERS = {
            "@mikro/arraydataset": MikroApi.aget_array_dataset,
            "@mikro/file": MikroApi.aget_file,
        }
"""

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from arkitekt_spec.scalars import ID


@runtime_checkable
class StructureClient(Protocol):
    """A client that expands and shrinks the structures of its service."""

    async def aexpand(self, identifier: str, id: ID) -> Any:
        """Expand ``id`` into the object of structure ``identifier``.

        Args:
            identifier: The structure's identifier, e.g. ``@mikro/arraydataset``.
            id: The id to expand.

        Returns:
            The object.
        """
        ...

    async def aexpand_many(self, identifier: str, ids: Sequence[ID]) -> Sequence[Any]:
        """Expand several ids of structure ``identifier``, in one request where possible.

        Args:
            identifier: The structure's identifier.
            ids: The ids to expand.

        Returns:
            One value per id, in order, ``None`` where there is no object.
        """
        ...

    async def ashrink(self, identifier: str, obj: Any) -> ID:
        """Shrink ``obj`` of structure ``identifier`` to the id it travels as.

        Args:
            identifier: The structure's identifier.
            obj: The object to shrink.

        Returns:
            Its id.
        """
        ...


__all__ = ["StructureClient"]
