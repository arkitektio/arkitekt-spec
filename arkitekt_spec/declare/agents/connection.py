"""What a run tells whoever started it: where its connection stands, and what its tasks do."""

import dataclasses
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any


class ConnectionState(str, Enum):
    """Where a run's connection to the backend stands."""

    CONNECTING = "connecting"
    """The run was started and is logging in or building its clients."""
    AWAITING_LOGIN = "awaiting_login"
    """A person has to approve the login: the device-code hook was just called."""
    REGISTERED = "registered"
    """The backend acknowledged the agent's registration (an ``Init``). Reported on
    the first connection and again whenever a dropped one is back."""
    DISCONNECTED = "disconnected"
    """The link dropped and the transport is trying to get it back."""
    FAILED = "failed"
    """The run ended on an error: the login was refused or the link could not be kept."""
    STOPPED = "stopped"
    """The run was cancelled, or ended by itself."""


ConnectionListener = Callable[[ConnectionState], Awaitable[None]]
"""Called with each change of a run's connection. It only reports: what it
raises is logged and dropped, never the run's problem.

An agent reports ``REGISTERED`` and ``DISCONNECTED``; the other states belong to
a detached run (``arkitekt.run_detached``), which is the only thing that knows
of a login or of its own end."""


class TaskEventKind(str, Enum):
    """What happened to a task an agent runs."""

    ASSIGNED = "assigned"
    """The agent took the task; the event carries its arguments."""
    PROGRESS = "progress"
    """It reported how far it is."""
    YIELDED = "yielded"
    """It produced a result (a generator may produce several)."""
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    """It was cancelled or interrupted before it finished."""


@dataclasses.dataclass(frozen=True)
class TaskEvent:
    """One thing that happened to one task."""

    task_id: str
    action: str
    """The interface the task runs on: by default the function's name."""
    kind: TaskEventKind
    arguments: dict[str, Any] | None = None
    """The arguments as they arrived (a structure is its id). Only on ``ASSIGNED``."""
    progress: int | None = None
    """Percent, on ``PROGRESS``."""
    message: str | None = None
    """What the task said with its progress."""
    error: str | None = None
    """Why it ended, on ``FAILED``."""


TaskListener = Callable[[TaskEvent], Awaitable[None]]
"""Called with each event of each task the agent runs. Like a
:data:`ConnectionListener` it only reports: what it raises is logged and dropped.

It is awaited where the agent reports, in order, so it has to return quickly:
hand the event to a queue or a signal and do the work elsewhere."""
