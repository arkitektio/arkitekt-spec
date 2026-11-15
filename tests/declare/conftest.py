from collections.abc import Iterator

import pytest

from arkitekt_spec.declare.structures.registry import StructureRegistry


@pytest.fixture()
def simple_registry() -> StructureRegistry:
    """A registry preloaded with the structures the test functions use.

    Nothing registers itself any more, so the structures a signature names have
    to be in here before a definition can be built against it.
    """
    from .funcs import Karl, LocalizedStructure
    from .structures import test_registry

    registry = test_registry()
    # Lives in funcs.py beside the function that returns it, and is the one thing
    # here that is kept on the shelve rather than fetched by id.
    registry.register_as_memory_structure(LocalizedStructure)
    # The one model the test functions name; a model is declared on an app.
    registry.model(Karl)
    return registry


@pytest.fixture(autouse=True)
def _catalogs_checked() -> Iterator[None]:
    """These tests declare bloks as an author's tooling loads them: held to their catalog too.

    An app that merely registers is held to the language alone (``test_catalog_checks.py``).
    """
    from arkitekt_spec.blok.catalogs import checking_catalogs, forget_catalogs

    forget_catalogs()
    with checking_catalogs():
        yield
    forget_catalogs()
