"""This module contains the exceptions used in the Agent."""


class AgentException(Exception):
    """
    Base class for all exceptions raised by the Agent.
    """


class ProvisionException(AgentException):
    """
    Base class for all exceptions raised by the Agent.
    """


class NoCallerError(AgentException):
    """This task cannot call other actions: nothing routes its calls anywhere.

    A local task (:meth:`~arkitekt_spec.declare.task.Task.local`) and a served app
    (server mode) have no agent registered with a server, so a call fails at once
    rather than waiting for an answer nobody will send.
    """


class StateRequirementsNotMet(AgentException):
    """
    Raised when the state requirements are not met
    """


class MissingServiceWarning(UserWarning):
    """A registered function uses a structure whose service the app does not have.

    A warning rather than an error: the function only fails if such a value is
    actually expanded, and an actor registered with ``bypass_expand`` never does.
    """
