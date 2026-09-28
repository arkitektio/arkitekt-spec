"""This module contains the exceptions used in the Agent."""


class AgentException(Exception):
    """
    Base class for all exceptions raised by the Agent.
    """


class ProvisionException(AgentException):
    """
    Base class for all exceptions raised by the Agent.
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
