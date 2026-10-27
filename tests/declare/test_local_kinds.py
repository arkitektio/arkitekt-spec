"""A local's kind: given, borrowed from an action's argument, implied by its default, or told by
its use in the tree; ``ANY`` when nothing tells."""

import warnings
from typing import Annotated

import pytest

from arkitekt_spec.blok.vocabulary import BlokError
from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.blok.declarations import Local
from arkitekt_spec.declare.blok.registry import resolved_locals
from arkitekt_spec.declare.catalogs import CatalogWarning
from arkitekt_spec.declare.annotations.markers import Description
from arkitekt_spec.declare.register import WrappedFunction


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


def built_locals(registry: AppRegistry, name: str = "panel"):
    """The locals the blok is sent with, resolved as a server stores them."""
    return resolved_locals(registry.get_declared_bloks()[name], registry)


def sent_locals(registry: AppRegistry, name: str = "panel"):
    """The locals the blok is sent with: as written."""
    return registry.get_declared_bloks()[name].locals or ()


def quietly(registry: AppRegistry, source: str, **locals_: object) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        registry.register_blok("panel", source, locals=locals_)  # pyright: ignore[reportArgumentType]


def test_a_local_borrows_an_arguments_port(registry: AppRegistry) -> None:
    deconvolve = register_deconvolve(registry)
    quietly(registry, "<Html><Slider bind={n} /></Html>", n=Local.like(deconvolve, "iterations", persist=True))

    (local,) = built_locals(registry)
    assert (local.key, local.kind, local.default, local.persist, local.description) == ("n", "INT", 15, True, "How often")


def test_a_borrowed_local_keeps_its_own_default_and_label(registry: AppRegistry) -> None:
    register_deconvolve(registry)
    quietly(registry, "<Html><Slider bind={n} /></Html>", n=Local.like("deconvolve", "iterations", default=3, label="Runs"))

    (local,) = built_locals(registry)
    assert (local.kind, local.default, local.label) == ("INT", 3, "Runs")


def test_a_borrowed_list_keeps_its_item_port(registry: AppRegistry) -> None:
    register_deconvolve(registry)
    quietly(registry, "<Html><Text text={flags} /></Html>", flags=Local.like("deconvolve", "flags"))

    (local,) = built_locals(registry)
    assert (local.kind, local.nullable, [child.kind for child in local.children or ()]) == ("LIST", True, ["STRING"])


@pytest.mark.parametrize(("action", "argument", "problem"), [("nope", "iterations", "is no action"), ("deconvolve", "nope", "has no argument 'nope'")])
def test_borrowing_what_the_app_does_not_register_is_refused(registry: AppRegistry, action: str, argument: str, problem: str) -> None:
    register_deconvolve(registry)
    with pytest.raises(BlokError) as caught:
        registry.register_blok("panel", "<Html><Text /></Html>", locals={"x": Local.like(action, argument)})
    assert caught.value.code == "local.like" and problem in str(caught.value)


def test_a_local_takes_the_full_port_of_the_self_argument_it_is_passed_to(registry: AppRegistry) -> None:
    register_deconvolve(registry)
    quietly(registry, "<Html><Button onClick={self.deconvolve(iterations: n)} /></Html>", n=Local(None))

    (local,) = built_locals(registry)
    assert (local.kind, local.default) == ("INT", None)
    (sent,) = sent_locals(registry)
    assert "kind" not in sent.model_fields_set


def test_an_action_registered_after_the_blok_still_types_its_local(registry: AppRegistry) -> None:
    quietly(registry, "<Html><Button onClick={self.deconvolve(mode: m)} /></Html>", m=Local(None))
    register_deconvolve(registry)

    (local,) = built_locals(registry)
    assert local.kind == "STRING"


def test_a_local_takes_the_kind_its_control_writes(registry: AppRegistry) -> None:
    quietly(registry, "<Html><Card><Switch bind={on} /><Slider bind={x} /><ToggleGroup bind={any} /></Card></Html>", on=Local(None), x=None, any=[])

    assert [(local.key, local.kind) for local in built_locals(registry)] == [("on", "BOOL"), ("x", "FLOAT"), ("any", "ANY")]


def test_a_type_beats_every_use(registry: AppRegistry) -> None:
    with pytest.raises(BlokError) as caught:
        registry.register_blok("panel", "<Html><Slider bind={x} /></Html>", locals={"x": Local[str]()})
    assert caught.value.code == "local.bind_kind"


def test_an_untyped_local_is_any_and_checked_against_nothing(registry: AppRegistry) -> None:
    register_deconvolve(registry)
    quietly(registry, "<Html><Text text={x.anything.below} /></Html>", x=Local(None))

    (local,) = built_locals(registry)
    assert local.kind == "ANY"


def test_a_kind_name_is_no_type(registry: AppRegistry) -> None:
    with pytest.raises(BlokError) as caught:
        registry.register_blok("panel", "<Html><Text /></Html>", locals={"n": Local(15, type="INT")})
    assert caught.value.code == "local.kind"


def test_an_unknown_module_is_a_warning(registry: AppRegistry) -> None:
    with pytest.warns(CatalogWarning, match=r"module.unknown"):
        registry.register_blok("panel", "<Html><Card /></Html>", module="mikro")


def test_the_orkestrator_module_is_known(registry: AppRegistry) -> None:
    quietly(registry, "<Html><Card /></Html>")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        registry.register_blok("other", "<Html><Card /></Html>", module="orkestrator")
