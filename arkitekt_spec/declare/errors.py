"""Custom exceptions for Rekuest."""


class RekuestError(Exception):
    """Base class for all Rekuest exceptions."""



class CriticalCallError(RekuestError):
    """Raised when a critical error occurs during a remote call."""



class ErrorCallError(RekuestError):
    """Raised when an error occurs during a remote call."""



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
