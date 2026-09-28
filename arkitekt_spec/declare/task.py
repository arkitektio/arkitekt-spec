"""The injectable task: what an action writes as ``task: Task``.

The declaration only needs to recognise the parameter, so it is not a port; the
runtime that executes the action (rekuest's agent, a server-mode runtime) hands in
its own implementation. That implementation sets :data:`TASK_MARKER` on its class.
"""

import logging
from typing import Any, ClassVar, Protocol, runtime_checkable

logger = logging.getLogger("arkitekt.task")

TASK_MARKER = "__arkitekt_task__"
"""The class attribute that makes a parameter receive the running task."""


@runtime_checkable
class Task(Protocol):
    """The task an action is running as: its identity, and how it reports progress."""

    __arkitekt_task__: ClassVar[bool] = True

    @property
    def id(self) -> str:
        ...

    def log(self, message: str, level: Any = ...) -> None:
        ...

    def progress(self, percent: int, message: str | None = None) -> None:
        ...

    async def alog(self, message: str, level: Any = ...) -> None:
        ...

    async def aprogress(self, percent: int, message: str | None = None) -> None:
        ...

    @classmethod
    def local(cls, id: str = "local", user: str = "local", org: str = "local") -> "LocalTask":
        """A task for calling an action directly, with no runtime behind it.

        ``segment(image, task=Task.local(), mikro=mikro)``: logs and progress go to
        the ``arkitekt.task`` logger, pause points return at once, and there is no
        assignment, agent or token -- so clients handed out for it attribute nothing.
        """
        return LocalTask(id=id, user=user, org=org)


class LocalTask:
    """The task of an action called directly: it only logs.

    Calling other actions needs a runtime (an agent to route through), which a
    local call does not have.
    """

    __arkitekt_task__: ClassVar[bool] = True

    assignment = None
    agent = None
    token = None

    def __init__(self, id: str = "local", user: str = "local", org: str = "local") -> None:
        """Make a local task, tagged ``id`` in its logs."""
        self.id = id
        self.user = user
        self.org = org

    @classmethod
    def local(cls, id: str = "local", user: str = "local", org: str = "local") -> "LocalTask":
        """Another local task (every task type answers ``local()``)."""
        return cls(id=id, user=user, org=org)

    def log(self, message: str, level: Any = None) -> None:
        """Log ``message`` under this task."""
        logger.log(_level(level), "[%s] %s", self.id, message)

    def progress(self, percent: int, message: str | None = None) -> None:
        """Report progress, as a log line."""
        logger.info("[%s] %s%% %s", self.id, int(percent), message or "")

    def pausepoint(self) -> None:
        """Nothing pauses a local call."""
        return

    async def alog(self, message: str, level: Any = None) -> None:
        """Log ``message`` under this task."""
        self.log(message, level)

    async def aprogress(self, percent: int, message: str | None = None) -> None:
        """Report progress, as a log line."""
        self.progress(percent, message)

    async def apausepoint(self) -> None:
        """Nothing pauses a local call."""
        return


def _level(level: Any) -> int:
    name = str(getattr(level, "value", level) or "INFO").upper()
    return logging.getLevelNamesMapping().get(name, logging.INFO)


def is_task(obj: object) -> bool:
    """Whether ``obj`` (possibly ``Optional``/``Annotated``) is a task class."""
    from arkitekt_spec.declare.agents.types import unwrap_injectable

    cls = unwrap_injectable(obj)
    return isinstance(cls, type) and getattr(cls, TASK_MARKER, False) is True


__all__ = ["TASK_MARKER", "LocalTask", "Task", "is_task"]
