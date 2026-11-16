"""Who holds a blok to its catalog: an app registering is held to the language alone."""

import warnings
from collections.abc import Iterator

import pytest

from arkitekt_spec.blok.catalogs import catalogs_checked, checking_catalogs
from arkitekt_spec.blok.vocabulary import BlokError
from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.catalogs import CatalogWarning

LAB = "https://acme.example/catalogs/lab/v1"


@pytest.fixture()
def registering() -> Iterator[AppRegistry]:
    """An app as it merely registers: catalogs not checked (the conftest turns them on)."""
    with checking_catalogs(False):
        yield AppRegistry()


def test_registering_checks_no_catalog(registering: AppRegistry) -> None:
    assert not catalogs_checked()
    with warnings.catch_warnings():
        warnings.simplefilter("error", CatalogWarning)
        registering.register_blok("unknown", '<Blok kind="agent" context="panel"><Frobnicator /></Blok>', mode="strict")
        registering.register_blok("props", '<Blok kind="agent" context="panel"><Button frob={1}>Go</Button></Blok>', mode="strict")
        registering.register_blok("another", '<Blok kind="agent" context="panel"><MikroSceneProbe /></Blok>', mode="strict")
        registering.register_blok("far", f'<Blok kind="agent" context="panel" catalog="{LAB}"><Card /></Blok>', mode="strict")
        registering.get_declared_bloks()


def test_registering_still_holds_a_blok_to_the_language(registering: AppRegistry) -> None:
    with pytest.raises(BlokError, match="roots.unknown"):
        registering.register_blok("root", '<Blok kind="agent" context="panel"><Text>{nothing.here}</Text></Blok>')
    with pytest.raises(BlokError, match="bsx.mismatched_tag"):
        registering.register_blok("markup", '<Blok kind="agent" context="panel"><Card></Blok>')
    with pytest.raises(BlokError, match="roots.not_in_kind"):
        registering.register_blok("task", '<Blok kind="agent" context="panel"><Button onClick={task.cancel()}>Stop</Button></Blok>')
    with pytest.raises(ValueError, match="manifest.catalog"):
        registering.register_blok("named", '<Blok kind="agent" context="panel" catalog="ui@1"><Card /></Blok>')


def test_a_blok_written_for_a_catalog_not_known_here_still_registers(registering: AppRegistry) -> None:
    """Its text stays as nodes: what its components do with it is their renderer's to say."""
    registering.register_blok("far", f'<Blok kind="agent" context="panel" catalog="{LAB}"><Spectrum>of a lab</Spectrum></Blok>')

    blok = registering.get_declared_bloks()["far"]
    assert blok.catalog == LAB and blok.source is not None


def test_tooling_turns_the_catalog_on_around_loading_the_app() -> None:
    with checking_catalogs(False):
        registry = AppRegistry()
        with checking_catalogs(), pytest.warns(CatalogWarning, match="catalog.unknown_component"):
            registry.register_blok("panel", '<Blok kind="agent" context="panel"><Frobnicator /></Blok>')
        assert not catalogs_checked()
