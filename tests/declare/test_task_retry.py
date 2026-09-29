"""``task.retry``: a call is made again only when its agent was lost and that is safe.

Safe means the lost step never started, or the caller says so (``if_started=True``). What
running it again would do (``AgentLost.effects``) is information, never the decision.
"""

import pytest

from arkitekt_spec.declare.errors import AgentLost, ErrorCallError
from arkitekt_spec.declare.task import Task, aretry, retry


def flaky(*outcomes: BaseException | str):
    """A call that raises or returns the given outcomes in turn, counting its calls."""
    calls: list[int] = []

    def call(x: int) -> str:
        calls.append(x)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    call.calls = calls  # type: ignore[attr-defined]
    return call


def test_a_step_that_never_started_is_tried_again() -> None:
    call = flaky(AgentLost(started=False), "done")

    assert retry(call, 3) == "done"
    assert call.calls == [3, 3]


def test_a_step_that_started_is_not_tried_again_unless_you_say_so() -> None:
    call = flaky(AgentLost(started=True, effects="NONE"), "done")

    with pytest.raises(AgentLost):
        retry(call, 3)
    assert call.calls == [3], "effects=NONE decides nothing"

    again = flaky(AgentLost(started=True), "done")
    assert retry(again, 3, if_started=True) == "done"


def test_it_gives_up_after_its_attempts() -> None:
    call = flaky(AgentLost(started=False), AgentLost(started=False), AgentLost(started=False, last_progress=10))

    with pytest.raises(AgentLost) as lost:
        retry(call, 3, attempts=3)
    assert lost.value.last_progress == 10 and len(call.calls) == 3


def test_a_failure_of_the_action_itself_is_not_retried() -> None:
    call = flaky(ErrorCallError("the action raised"), "done")

    with pytest.raises(ErrorCallError):
        retry(call, 3)
    assert call.calls == [3]


@pytest.mark.asyncio
async def test_the_async_form_awaits_the_call() -> None:
    calls: list[int] = []

    async def call(x: int) -> str:
        calls.append(x)
        if len(calls) == 1:
            raise AgentLost(started=False)
        return "done"

    assert await aretry(call, 4) == "done"
    assert calls == [4, 4]


def test_a_local_task_retries_the_same_way() -> None:
    call = flaky(AgentLost(started=False), "done")

    assert Task.local().retry(call, 5) == "done"
