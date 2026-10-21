"""A blok's args: the items it is opened for, declared like an action's and read under ``@args``."""

from typing import Annotated

import pytest

from arkitekt_spec.actions import DescriptorOperator, PortKind
from arkitekt_spec.declare.annotations import Requires
from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.structures.errors import StructureRegistryError


class Image:
    """An item a blok is opened for."""

    def __init__(self, id: str) -> None:
        self.id = id


Stack = Annotated[Image, Requires(key="@t/z_size", operator=DescriptorOperator.GTE, value=2)]
INSPECTOR = '<Text text="@args.image" />'


@pytest.fixture
def registry() -> AppRegistry:
    registry = AppRegistry()

    @registry.structure_registry.structure("@t/image")
    async def expand_image(id: str) -> Image:
        return Image(id)

    return registry


def test_args_are_ports_built_like_an_actions(registry: AppRegistry) -> None:
    registry.register_blok("inspector", '<article><Text text="@args.image" /><Text text="@args.mask" /></article>', args={"image": Stack, "mask": Image | None})

    blok = registry.get_declared_bloks()["inspector"]
    image, mask = blok.args
    assert (image.key, image.kind, image.identifier, image.nullable) == ("image", PortKind.STRUCTURE, "@t/image", False)
    assert image.requires and image.requires[0].key == "@t/z_size"
    assert mask.nullable
    # Null until the blok is opened for an item: the root the server takes from the demo state.
    assert blok.demo_state == {"args": {"image": None, "mask": None}}


def test_a_blok_without_args_sends_what_it_always_sent(registry: AppRegistry) -> None:
    registry.register_blok("panel", '<Text text="hello" />')

    blok = registry.get_declared_bloks()["panel"]
    assert "args" not in blok.model_dump(exclude_unset=True)
    agent = registry.to_implement_agent_input()
    assert "args" not in agent.model_dump(mode="json", by_alias=False, exclude_unset=True)["bloks"][0]
    # What the runtime hashes the agent by (``aget_hash``): unchanged too.
    assert "args" not in agent.model_dump(mode="json", by_alias=True, exclude_none=True)["bloks"][0]


def test_args_reach_the_wire(registry: AppRegistry) -> None:
    registry.register_blok("inspector", INSPECTOR, args={"image": Image})

    wire = registry.to_implement_agent_input().model_dump(mode="json", by_alias=False, exclude_unset=True)
    (blok,) = wire["bloks"]
    assert [port["identifier"] for port in blok["args"]] == ["@t/image"]
    assert blok["demo_state"] == {"args": {"image": None}}


def test_the_tree_reads_only_declared_args(registry: AppRegistry) -> None:
    with pytest.raises(ValueError, match=r"reads @args\.stack, but the blok declares no such arg \(declared: image\)"):
        registry.register_blok("inspector", '<Text text="@args.stack" />', args={"image": Image})
    with pytest.raises(ValueError, match="reads @args whole"):
        registry.register_blok("inspector", '<Text text="@args" />', args={"image": Image})


def test_without_args_there_is_no_args_root(registry: AppRegistry) -> None:
    """``@args`` is then an unknown dependency's state, as before bloks took args."""
    registry.register_blok("inspector", INSPECTOR)
    with pytest.raises(ValueError, match="unknown state 'image'"):
        registry.get_declared_bloks()


def test_only_an_html_blok_is_opened_for_an_item(registry: AppRegistry) -> None:
    with pytest.raises(ValueError, match="only an html blok is opened for an item"):
        registry.register_blok("view", '<Scene><Text text="@args.image" /></Scene>', context="scene", args={"image": Image})


@pytest.mark.parametrize("state", ["demo_state", "local_state"])
def test_the_args_root_is_the_bloks_own(registry: AppRegistry, state: str) -> None:
    with pytest.raises(ValueError, match="args are null until it is opened for an item"):
        registry.register_blok("inspector", INSPECTOR, args={"image": Image}, **{state: {"args": {"image": "x"}}})


def test_an_untyped_arg_is_refused(registry: AppRegistry) -> None:
    class Unknown:
        pass

    with pytest.raises(StructureRegistryError, match="is not registered"):
        registry.register_blok("inspector", INSPECTOR, args={"image": Unknown})
