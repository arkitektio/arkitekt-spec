"""Declared interactions: an interactions protocol's methods are typed asks and informs an action
registers with itself, and the dynamic flags say what a task may do at run time beyond what it
declared."""

from typing import Annotated, Literal, NamedTuple, Protocol

import pytest

from arkitekt_spec.blok.vocabulary import BlokError
from arkitekt_spec.declare.annotations import Key
from arkitekt_spec.declare.app import (
    DEPENDENCY_CALL_SCOPE,
    DYNAMIC_ASK_SCOPE,
    DYNAMIC_CALL_SCOPE,
    DYNAMIC_INFORM_SCOPE,
    AppRegistry,
)
from arkitekt_spec.declare.definition.errors import DefinitionError
from arkitekt_spec.declare.interactions import ask, inform

PICK = """<Html><Card><CardTitle text={args.question} />
  <Button label="Go" onClick={task.answer(values: {name: args.question, count: 2})} />
</Card></Html>"""


class Picked(NamedTuple):
    name: str
    count: int


class Asks(Protocol):
    @ask(PICK)
    def pick(self, question: str, error: str = "") -> Picked:
        """Pick one."""
        ...

    @ask('<Html><Button label="Ok" onClick={task.answer(values: {choice: "keep"})} /></Html>')
    def choose(self) -> Annotated[Literal["keep", "drop"], Key("choice")]:
        """Keep it?"""
        ...

    @inform("<Html><Card><CardTitle text={args.step} /></Card></Html>")
    def progress(self, step: int, total: int) -> None:
        """How far it got."""
        ...


def _registered(registry: AppRegistry) -> None:
    def asking(x: int, asks: Asks) -> int:
        """Asks."""
        return x

    registry.register_workflow(asking)


def test_an_action_registers_the_asks_it_is_handed() -> None:
    registry = AppRegistry()
    registry.register_interactions(Asks)
    _registered(registry)

    implementation = registry.implementations["asking"]
    assert [port.key for port in implementation.definition.args] == ["x"]
    asks = {ask.key: ask for ask in implementation.asks or ()}
    assert set(asks) == {"asks.pick", "asks.choose"}
    pick = asks["asks.pick"]
    assert pick.description == "Pick one."
    assert [(port.key, port.nullable) for port in pick.args] == [("question", False), ("error", True)]
    assert [(port.key, port.kind) for port in pick.returns] == [("name", "STRING"), ("count", "INT")]
    assert [(port.key, port.kind) for port in asks["asks.choose"].returns] == [("choice", "ENUM")]
    informs = {inform.key: inform for inform in implementation.informs or ()}
    assert set(informs) == {"asks.progress"}
    assert informs["asks.progress"].description == "How far it got."
    assert [port.key for port in informs["asks.progress"].args] == ["step", "total"]


def test_an_action_without_asks_or_flags_keeps_its_wire_bytes() -> None:
    registry = AppRegistry()

    def plain(x: int) -> int:
        """Plain."""
        return x

    registry.register_action(plain)
    dumped = registry.implementations["plain"].model_dump(exclude_none=True, by_alias=True)
    assert not {"asks", "informs", "dynamicAsks", "dynamicCalls", "dynamicInforms"} & dumped.keys()
    assert registry.get_scopes() == []


def test_an_answer_keyed_by_no_return_is_refused() -> None:
    registry = AppRegistry()

    class Bad(Protocol):
        @ask('<Html><Button onClick={task.answer(values: {nope: 1})} /></Html>')
        def pick(self) -> Picked: ...

    with pytest.raises(DefinitionError, match="'nope'"):
        registry.register_interactions(Bad)


def test_an_answer_leaving_out_a_required_return_is_refused() -> None:
    registry = AppRegistry()

    class Short(Protocol):
        @ask('<Html><Button onClick={task.answer(values: {name: "x"})} /></Html>')
        def pick(self) -> Picked: ...

    with pytest.raises(DefinitionError, match="'count'"):
        registry.register_interactions(Short)


def test_an_ask_reads_only_its_parameters_as_args() -> None:
    registry = AppRegistry()

    class Reads(Protocol):
        @ask('<Html><CardTitle text={args.other} /></Html>')
        def pick(self, question: str) -> None: ...

    with pytest.raises(BlokError):
        registry.register_interactions(Reads)


def test_a_public_method_without_ask_or_inform_is_refused() -> None:
    registry = AppRegistry()

    class Mixed(Protocol):
        @ask(PICK)
        def pick(self, question: str) -> Picked: ...

        def helper(self) -> None: ...

    with pytest.raises(DefinitionError, match="helper"):
        registry.register_interactions(Mixed)


def test_dynamic_calls_needs_a_workflow() -> None:
    registry = AppRegistry()

    def plain(x: int) -> int:
        """Plain."""
        return x

    with pytest.raises(DefinitionError, match="workflow"):
        registry.register_action(plain, dynamic_calls=True)


def test_the_dynamic_flags_ask_for_their_scopes() -> None:
    registry = AppRegistry()

    def flow(x: int) -> int:
        """Flow."""
        return x

    registry.register_workflow(flow, dynamic_asks=True, dynamic_calls=True, dynamic_informs=True)
    implementation = registry.implementations["flow"]
    assert (implementation.dynamic_asks, implementation.dynamic_calls, implementation.dynamic_informs) == (True, True, True)
    assert registry.get_scopes() == sorted([DYNAMIC_ASK_SCOPE, DYNAMIC_CALL_SCOPE, DYNAMIC_INFORM_SCOPE])


def test_an_inform_with_a_return_is_refused() -> None:
    registry = AppRegistry()

    class Returns(Protocol):
        @inform("<Html><Text text={args.step} /></Html>")
        def progress(self, step: int) -> int: ...

    with pytest.raises(DefinitionError, match="returns nothing"):
        registry.register_interactions(Returns)


def test_an_inform_answering_is_refused() -> None:
    registry = AppRegistry()

    class Answers(Protocol):
        @inform('<Html><Button onClick={task.answer(values: {x: 1})} /></Html>')
        def progress(self) -> None: ...

    with pytest.raises(DefinitionError, match="make it an @ask"):
        registry.register_interactions(Answers)


def test_calling_through_a_dependency_asks_for_its_scope() -> None:
    registry = AppRegistry()

    @registry.declare(app="other")
    class Other(Protocol):
        def move(self, x: int) -> int: ...

    def flow(x: int, other: Other) -> int:
        """Flow."""
        return x

    registry.register_workflow(flow)
    assert registry.get_scopes() == [DEPENDENCY_CALL_SCOPE]
