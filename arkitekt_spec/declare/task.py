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

import asyncio
import contextlib
import inspect
import logging
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Generator
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from arkitekt_spec.declare.agents.errors import NoCallerError
from arkitekt_spec.declare.errors import AgentLost
from arkitekt_spec.declare.structures.types import JSONSerializable
from arkitekt_spec.declare.targets import CallTarget, ImplementationTarget
from arkitekt_spec.scalars import ActionHash

logger = logging.getLogger("arkitekt.task")

TASK_MARKER = "__arkitekt_task__"
"""The class attribute that makes a parameter receive the running task."""


@dataclass(frozen=True)
class StateRef:
    """A dependency's state, named by the workflow (``handler.plate`` on a dependency proxy).

    What ``task.guard`` watches: it names the state, it does not hold its value.
    """

    dependency: str
    """The dependency's key: the workflow's parameter."""
    state: str
    """The state's attribute on the dependency's protocol."""


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

    def check_cancelled(self) -> None:
        """Stop here if the task was cancelled.

        A function that is not ``async`` only learns of a cancellation where it
        asks: here, and at :meth:`progress` and :meth:`log`. Call it between the
        steps of anything long -- before each move, each frame, each file -- so
        a cancelled task stops instead of running to its end.
        """
        ...

    def install_hook(self, hook: AssignmentHook) -> None:
        """Run ``hook`` when the task's assignment receives a message of its kind."""
        ...

    # -- effects ---------------------------------------------------------- #
    # A value the task takes from outside itself. The running task records each
    # one (an ``EFFECT`` in its history) so that a replay can return the same
    # value instead of taking a new one.

    def now(self) -> float:
        """The current time, in epoch seconds, recorded as the task's effect."""
        ...

    async def anow(self) -> float:
        """The current time, in epoch seconds, recorded as the task's effect."""
        ...

    def random(self, n: int = 16) -> str:
        """``n`` random bytes, as hex, recorded as the task's effect."""
        ...

    async def arandom(self, n: int = 16) -> str:
        """``n`` random bytes, as hex, recorded as the task's effect."""
        ...

    def sleep(self, seconds: float) -> None:
        """Sleep for ``seconds``: the deadline is recorded, then slept until."""
        ...

    async def asleep(self, seconds: float) -> None:
        """Sleep for ``seconds``: the deadline is recorded, then slept until."""
        ...

    def record(self, fn: Callable[[], Any], key: str | None = None) -> Any:
        """Take a value from outside the task through ``fn``, recorded so a resumed
        workflow gets the same one back instead of calling ``fn`` again. JSON only."""
        ...

    async def arecord(self, fn: Callable[[], Any], key: str | None = None) -> Any:
        """Take a value from outside the task through ``fn`` (awaited if it is a
        coroutine function), recorded so a resumed workflow gets the same one back."""
        ...

    # -- deciding about a lost step ---------------------------------------- #

    def retry(self, call: Callable[..., Any], *args: Any, attempts: int = 3, if_started: bool = False, **kwargs: Any) -> Any:
        """Call ``call(*args, **kwargs)``, again when its agent is lost (``AgentLost``).

        Only when that is safe: when the lost step never started, or when you say so with
        ``if_started=True``. Anything else is re-raised: that decision is yours.
        """
        ...

    async def aretry(self, call: Callable[..., Any], *args: Any, attempts: int = 3, if_started: bool = False, **kwargs: Any) -> Any:
        """:meth:`retry`, awaiting ``call``."""
        ...

    def guard(self, state: object, *paths: str) -> "contextlib.AbstractContextManager[None]":
        """Watch a dependency's state (the ``paths`` of it, or all of it) across a resume.

        ``state`` is the state attribute of a dependency (``handler.plate``): the protocol
        types it as the state's class, and the proxy hands over a :class:`StateRef`.

        On a resumed run, entering the guard raises ``StateChanged`` if anything other than
        this workflow's own calls changed it since the first run entered, or its agent
        restarted and set it up again.
        """
        ...

    def aguard(self, state: object, *paths: str) -> "contextlib.AbstractAsyncContextManager[None]":
        """:meth:`guard`, entered with ``async with``."""
        ...

    def hold(self, message: str, *, lost: AgentLost | None = None) -> None:
        """Wait for a person: the task pauses with ``message`` until someone resumes it
        (it carries on after the hold) or cancels it. ``lost`` puts what is known about a
        lost step (its effects, last progress) in front of whoever decides."""
        ...

    async def ahold(self, message: str, *, lost: AgentLost | None = None) -> None:
        """:meth:`hold`, awaited."""
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

    def check_cancelled(self) -> None:
        """Nothing cancels a local call."""
        return

    def install_hook(self, hook: AssignmentHook) -> None:
        """Keep the hook; nothing pauses a local task, so it never runs."""
        self.hooks.append(hook)

    def now(self) -> float:
        """The current time; nothing records it."""
        return time.time()

    async def anow(self) -> float:
        """The current time; nothing records it."""
        return self.now()

    def random(self, n: int = 16) -> str:
        """``n`` random bytes, as hex; nothing records them."""
        return secrets.token_hex(n)

    async def arandom(self, n: int = 16) -> str:
        """``n`` random bytes, as hex; nothing records them."""
        return self.random(n)

    def sleep(self, seconds: float) -> None:
        """Sleep for ``seconds``."""
        time.sleep(max(0.0, seconds))

    async def asleep(self, seconds: float) -> None:
        """Sleep for ``seconds``."""
        await asyncio.sleep(max(0.0, seconds))

    def record(self, fn: Callable[[], Any], key: str | None = None) -> Any:
        """``fn()``; nothing records it."""
        return fn()

    async def arecord(self, fn: Callable[[], Any], key: str | None = None) -> Any:
        """``fn()``, awaited if it is a coroutine function; nothing records it."""
        value = fn()
        return await value if inspect.isawaitable(value) else value

    def retry(self, call: Callable[..., Any], *args: Any, attempts: int = 3, if_started: bool = False, **kwargs: Any) -> Any:
        """Call it, again when its agent is lost and that is safe (see :meth:`Task.retry`)."""
        return retry(call, *args, attempts=attempts, if_started=if_started, **kwargs)

    async def aretry(self, call: Callable[..., Any], *args: Any, attempts: int = 3, if_started: bool = False, **kwargs: Any) -> Any:
        """Await it, again when its agent is lost and that is safe (see :meth:`Task.retry`)."""
        return await aretry(call, *args, attempts=attempts, if_started=if_started, **kwargs)

    @contextlib.contextmanager
    def guard(self, state: object, *paths: str) -> Generator[None, None, None]:
        """A local task is never resumed: nothing to watch."""
        yield

    @contextlib.asynccontextmanager
    async def aguard(self, state: object, *paths: str) -> AsyncIterator[None]:
        """A local task is never resumed: nothing to watch."""
        yield

    def hold(self, message: str, *, lost: AgentLost | None = None) -> None:
        """Nobody decides for a local task: it logs ``message`` and carries on, as if resumed."""
        logger.log(logging.WARNING, "hold (a local task carries on): %s", message)

    async def ahold(self, message: str, *, lost: AgentLost | None = None) -> None:
        """Nobody decides for a local task: it logs ``message`` and carries on, as if resumed."""
        self.hold(message, lost=lost)

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


def _should_retry(lost: "AgentLost", attempt: int, attempts: int, if_started: bool) -> bool:
    return attempt < attempts and (not lost.started or if_started)


def retry(call: Callable[..., Any], *args: Any, attempts: int = 3, if_started: bool = False, **kwargs: Any) -> Any:
    """Call ``call(*args, **kwargs)``, again when its agent is lost and that is safe.

    Safe means the lost step never started, or ``if_started=True``: you know running it
    again is fine. What running it again would do (``AgentLost.effects``) is information
    for you, never this function's decision. Other exceptions are not retried.
    """
    for attempt in range(1, attempts + 1):
        try:
            return call(*args, **kwargs)
        except AgentLost as lost:
            if not _should_retry(lost, attempt, attempts, if_started):
                raise
    raise AssertionError("unreachable: the last attempt returns or raises")


async def aretry(call: Callable[..., Any], *args: Any, attempts: int = 3, if_started: bool = False, **kwargs: Any) -> Any:
    """:func:`retry`, awaiting ``call``."""
    for attempt in range(1, attempts + 1):
        try:
            value = call(*args, **kwargs)
            return await value if inspect.isawaitable(value) else value
        except AgentLost as lost:
            if not _should_retry(lost, attempt, attempts, if_started):
                raise
    raise AssertionError("unreachable: the last attempt returns or raises")


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
