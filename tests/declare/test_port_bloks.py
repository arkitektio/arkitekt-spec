"""A port's own blok: the widget of one argument, checked where it is mounted and sent as its source."""

from dataclasses import dataclass
from typing import Annotated

import pytest

from arkitekt_spec.blok.vocabulary import BlokError
from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.widgets import PortBlok, SliderWidget


def port_blok(content: str) -> str:
    return f'<Blok kind="port" context="panel">{content}</Blok>'


EXPOSURE = port_blok(
    "<Slider bind={port.value} min={0} max={self.Camera.max_exposure} />"
    "<Button onClick={port.set(self.Camera.exposure)}>Use current</Button>"
    "<Text>{args.frames}</Text><message />"
)


@pytest.fixture
def registry() -> AppRegistry:
    """An app with a camera state."""
    registry = AppRegistry()

    @registry.state
    @dataclass
    class Camera:
        exposure: float
        max_exposure: float

    return registry


def acquire(registry: AppRegistry, source: str) -> None:
    """Register an action whose ``exposure`` port is drawn by the port blok ``source``."""

    @registry.register
    def acquire(
        exposure: Annotated[
            float, PortBlok(source, fallback=SliderWidget(min=0, max=100))
        ],
        frames: int = 3,
    ) -> int:
        """Acquire."""
        return frames


def test_a_port_blok_is_sent_as_its_source_on_the_ports_widget(
    registry: AppRegistry,
) -> None:
    acquire(registry, EXPOSURE)

    (implementation,) = registry.to_registered_agent_input().model_dump(
        mode="json", by_alias=False, exclude_unset=True
    )["implementations"]
    exposure, frames = implementation["definition"]["args"]
    assert exposure["widget"]["kind"] == "BLOK" and exposure["widget"]["vocab"] == 2
    assert exposure["widget"]["source"] == EXPOSURE
    assert exposure["widget"]["fallback"]["kind"] == "SLIDER"
    assert frames.get("widget") is None


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (
            "<Button onClick={self.acquire(exposure: 1)}>Go</Button>",
            "calls.not_in_kind",
        ),
        ("<Text>{args.gain}</Text>", "args.unknown"),
        ("<Slider bind={port.error} />", "port.bind"),
        ("<Text>{port.value.x}</Text>", "path.unknown"),
        ("<Text>{task.status}</Text>", "roots.not_in_kind"),
        ("<Text>{self.Stage.x}</Text>", "deps.unknown_state"),
    ],
)
def test_a_port_blok_is_a_field_of_its_actions_form(
    registry: AppRegistry, content: str, code: str
) -> None:
    """It calls no agent, reads only the arguments the action takes, writes only its port's value,
    which is typed as the port, and reads of the agent only what the app declares."""
    with pytest.raises(BlokError) as refused:
        acquire(registry, port_blok(content))
        registry.to_registered_agent_input()

    assert refused.value.code == code
    assert "widget of port 'exposure'" in str(refused.value)


def test_a_widget_that_reads_its_agent_makes_the_action_stateful(
    registry: AppRegistry,
) -> None:
    """It matters which agent runs it, so a caller names one; a widget of the form alone says nothing of that."""
    acquire(registry, EXPOSURE)
    assert registry.implementations["acquire"].definition.stateful is True

    plain = AppRegistry()
    acquire(plain, port_blok("<Slider bind={port.value} max={args.frames} />"))
    assert plain.implementations["acquire"].definition.stateful is False


def test_only_a_port_blok_is_a_ports_widget() -> None:
    with pytest.raises(BlokError) as refused:
        PortBlok('<Blok kind="agent" context="panel"><Card /></Blok>')

    assert refused.value.code == "kind.placement"


PORT_BLOK_SERVER_HASH = (
    "67e542b9c8430f8210a9d7da0f0db69bba38af6922a0deaaae5dc4822c6da893"
)


def test_a_port_blok_hashes_as_the_server_hashes_it() -> None:
    """Digest computed once by the rekuest server's `unique_hash` for the same definition (takt's
    is held to the server's by its fixtures): a port blok is part of what an action is."""
    from arkitekt_spec.actions import ArgPortInput, DefinitionInput, definition_hash

    source = '<Blok kind="port" context="panel"><Slider bind={port.value} max={args.limit} /><message /></Blok>'
    definition = DefinitionInput(
        key="k",
        name="n",
        version="0.1.0",
        kind="FUNCTION",
        args=(
            ArgPortInput(
                key="scale",
                kind="FLOAT",
                widget=PortBlok(source, fallback=SliderWidget(min=0, max=1)),
                label="s",
            ),
            ArgPortInput(key="limit", kind="INT", label="l"),
        ),
    )

    assert definition_hash(definition) == PORT_BLOK_SERVER_HASH


def test_a_choice_over_the_agents_own_state_makes_the_action_stateful() -> None:
    """The choices are that agent's, so it matters which one runs the action."""
    from arkitekt_spec.declare.widgets import withStateChoices

    registry = AppRegistry()

    @registry.state
    @dataclass
    class Robot:
        trajectories: list[str]

    @registry.register
    def run(
        trajectory: Annotated[str, withStateChoices("self.Robot.trajectories")],
    ) -> None:
        """Run."""

    assert registry.implementations["run"].definition.stateful is True
