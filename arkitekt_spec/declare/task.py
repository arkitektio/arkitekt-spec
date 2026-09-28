"""The injectable task: what an action writes as ``task: Task``.

:class:`Task` is everything an action may do with the task it runs as: know who it
runs for, report logs and progress, stop at pause points, and call other actions as
its children. The declaration only needs to recognise the parameter (it is injected,
not a port); the runtime that executes the action hands in its own implementation,
which sets :data:`TASK_MARKER` on its class.

An action called directly, with no runtime behind it, takes :meth:`Task.local`: a
:class:`LocalTask` that logs, never pauses, and refuses calls with
:class:`~arkitekt_spec.declare.agents.errors.NoCallerError`.
"""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Generator
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from arkitekt_spec.declare.agents.errors import NoCallerError
from arkitekt_spec.declare.structures.types import JSONSerializable
from arkitekt_spec.declare.targets import CallTarget, ImplementationTarget
from arkitekt_spec.scalars import ActionHash

logger = logging.getLogger("arkitekt.task")

TASK_MARKER = "__arkitekt_task__"
"""The class attribute that makes a parameter receive the running task."""


class LogLevel(str, Enum):
    """How loud a task's log line is."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    ERROR = "ERROR"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


class HookKind(str, Enum):
    """When a server-side hook runs, relative to the task it is attached to."""

    CLEANUP = "CLEANUP"
    INIT = "INIT"
    __str__ = str.__str__


class HookInput(BaseModel):
    """An action the server runs at a lifecycle point of a task (its ``kind``)."""

    kind: HookKind = Field(description="When the hook runs.")
    hash: ActionHash = Field(description="The hash of the action that is the hook.")
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True)


@dataclass
class AssignmentHook:
    """Code a task runs when its assignment receives a message of ``kind``.

    ``kind`` is ``"pause"`` or ``"unpause"``; ``hook`` is awaited with the message
    (e.g. to stop a device when the user pauses the task).
    """

    id: str
    kind: str
    hook: Callable[[Any], Awaitable[None]]


@runtime_checkable
class Task(Protocol):
    """The task an action runs as: who it is for, what it reports, what it calls."""

    __arkitekt_task__: ClassVar[bool] = True

    @property
    def id(self) -> str:
        """The task's id."""
        ...

    @property
    def user(self) -> str:
        """The user the task runs for."""
        ...

    @property
    def org(self) -> str:
        """The organization the task runs in."""
        ...

    @property
    def token(self) -> str | None:
        """The task's provenance token, if it has one."""
        ...

    # -- reporting -------------------------------------------------------- #

    def log(self, message: str, level: LogLevel = ...) -> None:
        """Log ``message`` under this task."""
        ...

    async def alog(self, message: str, level: LogLevel = ...) -> None:
        """Log ``message`` under this task."""
        ...

    def progress(self, percentage: int, message: str | None = None) -> None:
        """Report how far the task is, in percent."""
        ...

    async def aprogress(self, percentage: int, message: str | None = None) -> None:
        """Report how far the task is, in percent."""
        ...

    def pausepoint(self) -> None:
        """Pause here if the task was asked to."""
        ...

    async def apausepoint(self) -> None:
        """Pause here if the task was asked to."""
        ...

    def install_hook(self, hook: AssignmentHook) -> None:
        """Run ``hook`` when the task's assignment receives a message of its kind."""
        ...

    # -- calling ---------------------------------------------------------- #

    def call(
        self,
        target: CallTarget | ImplementationTarget,
        *args: Any,  # the action's own arguments
        **kwargs: Any,  # ditto, by keyword
    ) -> Any:  # whatever the action returns
        """Call an action as a child of this task, blocking for its result.

        Raises:
            NoCallerError: If nothing routes this task's calls (a local task, a
                served app).
        """
        ...

    async def acall(
        self,
        target: CallTarget | ImplementationTarget,
        *args: Any,  # the action's own arguments
        reference: str | None = None,
        hooks: list[HookInput] | None = None,
        capture: bool = False,
        escalate_to_interrupt: bool = False,
        cancel_timeout: float | None = None,
        **kwargs: Any,  # ditto, by keyword
    ) -> Any:  # whatever the action returns
        """Call an action as a child of this task.

        ``target`` is an already-fetched ``Action`` or ``Implementation``: a task knows
        no client, so it cannot look one up.

        Raises:
            NoCallerError: If nothing routes this task's calls.
        """
        ...

    def iterate(
        self,
        target: CallTarget | ImplementationTarget,
        *args: Any,  # the action's own arguments
        **kwargs: Any,  # ditto, by keyword
    ) -> Generator[Any, None, None]:
        """Stream a generator action's yields as a child of this task, blocking between them.

        Raises:
            NoCallerError: If nothing routes this task's calls.
        """
        ...

    def aiterate(
        self,
        target: CallTarget | ImplementationTarget,
        *args: Any,  # the action's own arguments
        reference: str | None = None,
        hooks: list[HookInput] | None = None,
        capture: bool = False,
        escalate_to_interrupt: bool = False,
        cancel_timeout: float | None = None,
        **kwargs: Any,  # ditto, by keyword
    ) -> AsyncIterator[Any]:
        """Stream a generator action's yields as a child of this task.

        Raises:
            NoCallerError: If nothing routes this task's calls.
        """
        ...

    async def acall_raw(
        self,
        kwargs: dict[str, JSONSerializable] | None = None,
        *,
        action: CallTarget | None = None,
        implementation: ImplementationTarget | None = None,
        reference: str | None = None,
        hooks: list[HookInput] | None = None,
        capture: bool = False,
        escalate_to_interrupt: bool = False,
        cancel_timeout: float | None = None,
    ) -> Any:  # the backend's raw payload
        """Call with already-serialized arguments, as a child of this task.

        Nothing is shrunk or expanded: ``kwargs`` goes out as it is, and what comes
        back is the backend's payload.

        Raises:
            NoCallerError: If nothing routes this task's calls.
        """
        ...

    def aiterate_raw(
        self,
        kwargs: dict[str, JSONSerializable] | None = None,
        *,
        action: CallTarget | None = None,
        implementation: ImplementationTarget | None = None,
        reference: str | None = None,
        hooks: list[HookInput] | None = None,
        capture: bool = False,
        escalate_to_interrupt: bool = False,
        cancel_timeout: float | None = None,
    ) -> AsyncIterator[Any]:
        """Stream with already-serialized arguments, as a child of this task.

        Raises:
            NoCallerError: If nothing routes this task's calls.
        """
        ...

    @classmethod
    def local(cls, id: str = "local", user: str = "local", org: str = "local") -> "Task":
        """A task for calling an action directly, with no runtime behind it.

        ``segment(image, task=Task.local(), mikro=mikro)``: logs and progress go to the
        ``arkitekt.task`` logger, pause points return at once, and calls raise
        :class:`~arkitekt_spec.declare.agents.errors.NoCallerError`.
        """
        return LocalTask(id=id, user=user, org=org)


def _no_caller(task_id: str) -> NoCallerError:
    return NoCallerError(
        f"Task {task_id!r} is local: it runs for no agent, so a call made through it has "
        "nothing to route it and nothing to be a child of. Run the app (arkitekt.run) to "
        "call as a child, or call through a client -- rekuest.call(action, ...) -- which "
        "makes a root."
    )


class LocalTask:
    """The task of an action called directly: it logs, never pauses, and cannot call."""

    __arkitekt_task__: ClassVar[bool] = True

    token: str | None = None

    def __init__(self, id: str = "local", user: str = "local", org: str = "local") -> None:
        """Make a local task, tagged ``id`` in its logs."""
        self.id = id
        self.user = user
        self.org = org
        self.hooks: list[AssignmentHook] = []

    @classmethod
    def local(cls, id: str = "local", user: str = "local", org: str = "local") -> "LocalTask":
        """Another local task (every task type answers ``local()``)."""
        return cls(id=id, user=user, org=org)

    def log(self, message: str, level: LogLevel = LogLevel.INFO) -> None:
        """Log ``message`` under this task."""
        logger.log(_level(level), "[%s] %s", self.id, message)

    async def alog(self, message: str, level: LogLevel = LogLevel.INFO) -> None:
        """Log ``message`` under this task."""
        self.log(message, level)

    def progress(self, percentage: int, message: str | None = None) -> None:
        """Report progress, as a log line."""
        logger.info("[%s] %s%% %s", self.id, int(percentage), message or "")

    async def aprogress(self, percentage: int, message: str | None = None) -> None:
        """Report progress, as a log line."""
        self.progress(percentage, message)

    def pausepoint(self) -> None:
        """Nothing pauses a local call."""
        return

    async def apausepoint(self) -> None:
        """Nothing pauses a local call."""
        return

    def install_hook(self, hook: AssignmentHook) -> None:
        """Keep the hook; nothing pauses a local task, so it never runs."""
        self.hooks.append(hook)

    def call(
        self,
        target: CallTarget | ImplementationTarget,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Refuse: a local task cannot call.

        Raises:
            NoCallerError: Always.
        """
        raise _no_caller(self.id)

    async def acall(
        self,
        target: CallTarget | ImplementationTarget,
        *args: Any,
        reference: str | None = None,
        hooks: list[HookInput] | None = None,
        capture: bool = False,
        escalate_to_interrupt: bool = False,
        cancel_timeout: float | None = None,
        **kwargs: Any,
    ) -> Any:
        """Refuse: a local task cannot call.

        Raises:
            NoCallerError: Always.
        """
        raise _no_caller(self.id)

    def iterate(
        self,
        target: CallTarget | ImplementationTarget,
        *args: Any,
        **kwargs: Any,
    ) -> Generator[Any, None, None]:
        """Refuse: a local task cannot call.

        Raises:
            NoCallerError: Always.
        """
        raise _no_caller(self.id)

    async def aiterate(
        self,
        target: CallTarget | ImplementationTarget,
        *args: Any,
        reference: str | None = None,
        hooks: list[HookInput] | None = None,
        capture: bool = False,
        escalate_to_interrupt: bool = False,
        cancel_timeout: float | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """Refuse: a local task cannot call.

        Raises:
            NoCallerError: Always.
        """
        raise _no_caller(self.id)
        yield  # an async generator, like every task's aiterate

    async def acall_raw(
        self,
        kwargs: dict[str, JSONSerializable] | None = None,
        *,
        action: CallTarget | None = None,
        implementation: ImplementationTarget | None = None,
        reference: str | None = None,
        hooks: list[HookInput] | None = None,
        capture: bool = False,
        escalate_to_interrupt: bool = False,
        cancel_timeout: float | None = None,
    ) -> Any:
        """Refuse: a local task cannot call.

        Raises:
            NoCallerError: Always.
        """
        raise _no_caller(self.id)

    async def aiterate_raw(
        self,
        kwargs: dict[str, JSONSerializable] | None = None,
        *,
        action: CallTarget | None = None,
        implementation: ImplementationTarget | None = None,
        reference: str | None = None,
        hooks: list[HookInput] | None = None,
        capture: bool = False,
        escalate_to_interrupt: bool = False,
        cancel_timeout: float | None = None,
    ) -> AsyncIterator[Any]:
        """Refuse: a local task cannot call.

        Raises:
            NoCallerError: Always.
        """
        raise _no_caller(self.id)
        yield  # an async generator, like every task's aiterate_raw


def _level(level: LogLevel | str) -> int:
    name = str(getattr(level, "value", level) or "INFO").upper()
    return logging.getLevelNamesMapping().get(name, logging.INFO)


def is_task(obj: object) -> bool:
    """Whether ``obj`` (possibly ``Optional``/``Annotated``) is a task class."""
    from arkitekt_spec.declare.agents.types import unwrap_injectable

    cls = unwrap_injectable(obj)
    return isinstance(cls, type) and getattr(cls, TASK_MARKER, False) is True


if TYPE_CHECKING:  # the type checker proves LocalTask is a Task

    def _local_task_is_a_task(task: LocalTask) -> Task:
        return task


__all__ = [
    "TASK_MARKER",
    "AssignmentHook",
    "HookInput",
    "HookKind",
    "LocalTask",
    "LogLevel",
    "Task",
    "is_task",
]
