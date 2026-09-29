"""Custom exceptions for Rekuest."""


class RekuestError(Exception):
    """Base class for all Rekuest exceptions."""



class CriticalCallError(RekuestError):
    """Raised when a critical error occurs during a remote call."""



class ErrorCallError(RekuestError):
    """Raised when an error occurs during a remote call."""



class NonDeterministicWorkflow(RekuestError):
    """A resumed workflow took a different path than the run it resumes.

    A workflow is resumed by running its code again against what the first run recorded;
    that only works if the code takes the same path. Here it asked for a value of one
    kind where the first run recorded another, or called a different action under the
    same call key. Outside values belong in ``task.record(...)`` or a call.
    """



class StateChanged(RekuestError):
    """A guarded state changed while the workflow was down: something other than its own
    calls changed it, or its agent restarted and set it up again. ``task.guard`` raises it
    when a resumed workflow enters the guard again."""



class NotAWorkflowError(RekuestError):
    """A plain action called another action. Only a workflow may: ``@app.workflow``."""



class AgentLost(RekuestError):
    """The agent running a call died while it ran. Not a failure: how it ended is unknown.

    Raised at the call, for whoever made it to decide: send it again, go on without it,
    or ask a person (``task.hold``). What is known comes with it:

    Attributes:
        started: Whether the task was ever picked up. If not, nothing ran, and sending it
            again is always safe.
        last_progress: The last progress it reported, if any.
        effects: What running it again would do to the world (the implementation's
            claim, ``"UNKNOWN"`` when it made none). Information, not a rule.
        task: The id of the lost task.
    """

    def __init__(
        self,
        message: str | None = None,
        *,
        started: bool = True,
        last_progress: int | None = None,
        effects: str | None = None,
        task: str | None = None,
    ) -> None:
        super().__init__(message or "The agent running this call died while it ran.")
        self.started = started
        self.last_progress = last_progress
        self.effects = effects or "UNKNOWN"
        self.task = task

    @classmethod
    def from_details(cls, details: dict | None, *, message: str | None = None, task: str | None = None) -> "AgentLost":
        """Build it from what the server recorded on the LOST event."""
        details = details or {}
        return cls(
            details.get("reason") or message,
            started=bool(details.get("started", True)),
            last_progress=details.get("last_progress"),
            effects=details.get("effects"),
            task=task,
        )



class RootOnlyCallError(RekuestError):
    """Raised when a call is made through the client while a task is running.

    A call through a :class:`~rekuest.client.client.Rekuest` is a *root*: it goes over the
    client's own postman with no parent. Inside a running task a call is that task's
    child, over the agent's socket and parented to its assignment -- which only the task
    knows -- so the client refuses rather than quietly making a sibling of the task it
    should have been a child of.

    The client-side counterpart of
    :class:`~rekuest.postmans.errors.RootOnlyAssignError`, which is the transport
    refusing the same mistake from the other end.
    """


class AppContextError(RekuestError):
    """A run's app context does not fit what the app declared.

    An app declares at most one app-context class; a run must then pass an
    instance of it (``run(app, context=...)``), and a run of an app that
    declares none must pass nothing. Raised before the agent starts, so the
    mismatch never reaches a hook or an action.
    """


class RegistryFrozenError(RekuestError):
    """Raised when something is registered on an app that is already running.

    An app is configured, then entered. What it offers is fixed at the moment it
    connects, because that is what it told the server; registering afterwards would
    add something the server never heard about and no caller can reach.
    """
