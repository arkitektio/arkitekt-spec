"""Binding a run's clients into a hand-registered structure's functions.

The clients a structure wants are the union over its expander, shrinker and
batch expander. Each function must still be handed only the ones it names: a
shrinker that takes the object alone (mikro's `@mikro/tabledataset`) beside an
expander that wants `mikro` was handed `mikro=` and failed every return.
"""

import pytest

from arkitekt_spec.declare.structures.registry import StructureRegistry
from arkitekt_spec.declare.structures.types import FullFilledStructure, Shrinker


class Library:
    """A client a run hands out."""

    def __init__(self, name: str) -> None:
        self.name = name


class Book:
    def __init__(self, id: str, shelf: str = "") -> None:
        self.id = id
        self.shelf = shelf


async def expand_book(id: str, library: Library) -> Book:
    return Book(id, library.name)


async def shrink_book(book: Book) -> str:
    """Wants no client: the id is on the object."""
    return f"book-{book.id}"


async def shrink_book_through(book: Book, library: Library) -> str:
    return f"{library.name}/{book.id}"


def bound(shrink: Shrinker) -> FullFilledStructure:
    registry = StructureRegistry(clients={"library": Library})
    registry.register_as_structure(Book, "@test/book", aexpand=expand_book, ashrink=shrink)
    registry.bind({"library": Library("central")})
    return registry.get_fullfilled_structure("@test/book")


@pytest.mark.asyncio
async def test_a_shrinker_that_names_no_client_is_handed_none() -> None:
    structure = bound(shrink_book)
    assert await structure.shrink(Book("7")) == "book-7"
    # The expander still gets its client.
    assert (await structure.expand("7")).shelf == "central"


@pytest.mark.asyncio
async def test_a_shrinker_that_names_a_client_is_handed_it() -> None:
    assert await bound(shrink_book_through).shrink(Book("7")) == "central/7"
