"""An action can be called directly, with a task that only logs."""

import logging

from arkitekt_spec.declare.task import LocalTask, Task, is_task


def test_a_local_task_is_a_task_and_only_logs(caplog):
    task = Task.local(id="t1")
    assert isinstance(task, LocalTask) and isinstance(task, Task)
    assert is_task(LocalTask) and is_task(Task)
    assert task.agent is None and task.token is None and task.assignment is None
    with caplog.at_level(logging.INFO, logger="arkitekt.task"):
        task.log("hello")
        task.progress(50, "half")
    assert "[t1] hello" in caplog.text and "50%" in caplog.text
    assert task.pausepoint() is None
