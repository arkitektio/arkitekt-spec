"""A local's kind, resolved where an app mounts it: borrowed from one of its actions' arguments
(``like``), or told by the self argument it is passed to; the rules themselves are the corpus's."""

import warnings
from typing import Annotated

import pytest

from arkitekt_spec.blok.vocabulary import BlokError
from arkitekt_spec.declare.annotations.markers import Description
from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.blok.registry import resolved_locals
from arkitekt_spec.declare.catalogs import CatalogWarning
from arkitekt_spec.declare.register import WrappedFunction

PANEL = '<Blok kind="agent" context="panel">'
LOCAL = "Blok[0]/local[0]"


@pytest.fixture
def registry() -> AppRegistry:
    return AppRegistry()


def register_deconvolve(registry: AppRegistry) -> WrappedFunction[..., object]:
    @registry.register
    def deconvolve(iterations: Annotated[int, Description("How often")] = 15, mode: str = "fast", flags: list[str] | None = None) -> float:
        """Deconvolve.

        Returns:
            float: The residual.
        """
        return 0.0

    return deconvolve  # pyright: ignore[reportReturnType]


def built_local(registry: AppRegistry, name: str = "panel"):
    """The blok's one local, resolved as a server stores it."""
    return resolved_locals(registry.get_declared_bloks()[name], registry)[LOCAL]


def quietly(registry: AppRegistry, source: str) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        registry.register_blok("panel", source)


def test_a_local_borrows_an_arguments_port(registry: AppRegistry) -> None:
    register_deconvolve(registry)
    quietly(registry, PANEL + '<local name="n" like="self.deconvolve.iterations" persist /><Slider bind={n} /><Button onClick={self.deconvolve(iterations: n)} /></Blok>')

    local = built_local(registry)
    assert (local.key, local.kind, local.default, local.persist, local.description) == ("n", "INT", 15, True, "How often")


def test_a_borrowed_local_keeps_its_own_default_and_label(registry: AppRegistry) -> None:
    register_deconvolve(registry)
    quietly(registry, PANEL + '<local name="n" like="self.deconvolve.iterations" default={3} label="Runs" /><Slider bind={n} /><Button onClick={self.deconvolve(iterations: n)} /></Blok>')

    local = built_local(registry)
    assert (local.kind, local.default, local.label) == ("INT", 3, "Runs")


def test_a_borrowed_list_keeps_its_item_port(registry: AppRegistry) -> None:
    register_deconvolve(registry)
    quietly(registry, PANEL + '<local name="flags" like="self.deconvolve.flags" /><Text text={flags} /><Button onClick={self.deconvolve(flags: flags)} /></Blok>')

    local = built_local(registry)
    assert (local.kind, local.nullable, [child.kind for child in local.children or ()]) == ("LIST", True, ["STRING"])


@pytest.mark.parametrize(("like", "problem"), [("self.nope.iterations", "names the action 'nope'"), ("self.deconvolve.nope", "names the argument 'nope'")])
def test_borrowing_what_the_app_does_not_register_is_refused(registry: AppRegistry, like: str, problem: str) -> None:
    register_deconvolve(registry)
    with pytest.raises(BlokError) as caught:
        registry.register_blok("panel", PANEL + f'<local name="x" like="{like}" /><Text /></Blok>')
    assert caught.value.code == "local.like" and problem in str(caught.value)


def test_a_local_takes_the_full_port_of_the_self_argument_it_is_passed_to(registry: AppRegistry) -> None:
    register_deconvolve(registry)
    quietly(registry, PANEL + '<local name="n" /><Button onClick={self.deconvolve(iterations: n)} /></Blok>')

    local = built_local(registry)
    assert (local.kind, local.default) == ("INT", None)


def test_an_action_registered_after_the_blok_still_types_its_local(registry: AppRegistry) -> None:
    quietly(registry, PANEL + '<local name="m" /><Button onClick={self.deconvolve(mode: m)} /></Blok>')
    register_deconvolve(registry)

    assert built_local(registry).kind == "STRING"


def test_a_catalog_not_known_here_is_a_warning(registry: AppRegistry) -> None:
    with pytest.warns(CatalogWarning, match=r"catalog.unknown"):
        registry.register_blok("panel", '<Blok kind="agent" context="panel" catalog="https://acme.example/catalogs/lab/v1"><Card /><Spectrum>of a lab</Spectrum></Blok>')


def test_the_orkestrators_catalog_is_known(registry: AppRegistry) -> None:
    from arkitekt_spec.blok.catalogs import ORKESTRATOR_CATALOG_ID

    quietly(registry, PANEL + "<Card /></Blok>")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        registry.register_blok("other", f'<Blok kind="agent" context="panel" catalog="{ORKESTRATOR_CATALOG_ID}"><Card /></Blok>')
