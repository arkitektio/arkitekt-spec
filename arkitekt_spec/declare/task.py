"""The injectable task: what an action writes as ``task: Task``.

The declaration only needs to recognise the parameter, so it is not a port; the
runtime that executes the action (rekuest's agent, a server-mode runtime) hands in
its own implementation. That implementation sets :data:`TASK_MARKER` on its class.
"""

from typing import Any, ClassVar, Protocol, runtime_checkable

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


def is_task(obj: object) -> bool:
    """Whether ``obj`` (possibly ``Optional``/``Annotated``) is a task class."""
    from arkitekt_spec.declare.agents.types import unwrap_injectable

    cls = unwrap_injectable(obj)
    return isinstance(cls, type) and getattr(cls, TASK_MARKER, False) is True


__all__ = ["TASK_MARKER", "Task", "is_task"]
