"""An action can be called directly, with a task that only logs."""

import asyncio
import logging
import time

import pytest

from arkitekt_spec.declare.agents.errors import NoCallerError
from arkitekt_spec.declare.task import AssignmentHook, LocalTask, LogLevel, Task, is_task


class Target:
    """The least a call target is: an id and the ports to serialize by."""

    id = "action-1"
    args = ()
    returns = ()


def test_a_local_task_is_a_task_and_only_logs(caplog: pytest.LogCaptureFixture) -> None:
    task = Task.local(id="t1")
    assert isinstance(task, LocalTask) and isinstance(task, Task)
    assert is_task(LocalTask) and is_task(Task)
    assert (task.id, task.user, task.org, task.token) == ("t1", "local", "local", None)
    with caplog.at_level(logging.INFO, logger="arkitekt.task"):
        task.log("hello")
        task.log("careful", LogLevel.WARN)
        task.progress(50, "half")
    assert "[t1] hello" in caplog.text and "50%" in caplog.text
    assert [r.levelno for r in caplog.records][:2] == [logging.INFO, logging.WARNING]
    assert task.pausepoint() is None


def test_a_local_task_reports_asynchronously_too(caplog: pytest.LogCaptureFixture) -> None:
    task = Task.local(id="t2")

    async def report() -> None:
        await task.alog("hello")
        await task.aprogress(10)
        assert await task.apausepoint() is None

    with caplog.at_level(logging.INFO, logger="arkitekt.task"):
        asyncio.run(report())
    assert "[t2] hello" in caplog.text


def test_a_local_task_keeps_its_hooks_but_never_runs_them() -> None:
    async def on_pause(message: object) -> None:
        raise AssertionError("nothing pauses a local task")

    task = LocalTask()
    hook = AssignmentHook(id="h", kind="pause", hook=on_pause)
    task.install_hook(hook)
    assert task.hooks == [hook]


def test_a_local_task_refuses_to_call() -> None:
    task = Task.local(id="t3")
    with pytest.raises(NoCallerError, match="'t3' is local"):
        task.call(Target(), 1)
    with pytest.raises(NoCallerError):
        task.iterate(Target(), 1)


def test_a_local_task_refuses_to_call_asynchronously() -> None:
    task = Task.local()

    async def call() -> None:
        await task.acall(Target(), 1)

    async def iterate() -> None:
        async for _ in task.aiterate(Target(), 1):
            pass

    async def call_raw() -> None:
        await task.acall_raw({"x": 1}, action=Target())

    async def iterate_raw() -> None:
        async for _ in task.aiterate_raw({"x": 1}, action=Target()):
            pass

    for refused in (call, iterate, call_raw, iterate_raw):
        with pytest.raises(NoCallerError):
            asyncio.run(refused())


def test_a_local_task_takes_effects_without_recording_them() -> None:
    task = Task.local()
    before = time.time()
    assert before <= task.now() <= time.time()
    assert len(task.random(4)) == 8 and int(task.random(4), 16) >= 0
    task.sleep(0.001)

    async def effects() -> tuple[float, str]:
        await task.asleep(0.001)
        return await task.anow(), await task.arandom(2)

    now, drawn = asyncio.run(effects())
    assert now >= before and len(drawn) == 4
    assert isinstance(task, Task), "still the spec's Task"
