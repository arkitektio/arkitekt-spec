"""The ``policy`` decorator for agent-dependency protocol methods.

A method on a :func:`~arkitekt_spec.declare.declare.declare` protocol is a call this app makes to
another. How the app wants such a call treated is the caller's to say, and it says it where the
dependency is declared, once, rather than at every place it calls::

    @app.declare(app="fake-ot2")
    class OT2Like(Protocol):
        @policy(inquire_after="5s", on_lost="FAIL")
        def run_protocol(self, protocol: str) -> None: ...

Nothing here is a demand on the other app: it does not travel with the declaration, and the
server never matches on it. It is what this app's own calls of the method carry.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

CALL_POLICY_ATTR = "__arkitekt_call_policy__"

#: What ``on_lost`` may say.
ON_LOST = ("FAIL", "RESUME", "RESTART")

_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h)\s*$")


def seconds(duration: float | str) -> float:
    """A duration in seconds: a number of them, or ``"500ms"``, ``"5s"``, ``"2m"``, ``"1h"``.

    Raises:
        ValueError: If it is negative or not one of those spellings.
    """
    if isinstance(duration, bool):
        raise ValueError(
            f"A duration is a number of seconds or a string like '5s', not {duration!r}."
        )
    if isinstance(duration, (int, float)):
        value = float(duration)
    else:
        match = _DURATION.match(duration)
        if match is None:
            raise ValueError(
                f"{duration!r} is not a duration. Write a number of seconds, or '500ms', '5s', '2m', '1h'."
            )
        value = float(match.group(1)) * _UNITS[match.group(2)]
    if value < 0:
        raise ValueError(f"A duration cannot be negative: {duration!r}.")
    return value


@dataclass(frozen=True)
class CallPolicy:
    """How an app wants its calls of one dependency method treated.

    Every field left ``None`` says nothing: the call's own argument, then the postman's
    default, then the server's, decides.

    Attributes:
        inquire_after: Seconds of silence from the task after which the call asks the task's
            agent whether it is still on it (``0`` never asks).
        on_lost: What becomes of the task should its agent be lost while it runs: ``"FAIL"``,
            ``"RESUME"`` (a workflow) or ``"RESTART"`` (an action declared idempotent).
        max_resumes: How often it may be sent again before it ends lost after all.
    """

    inquire_after: float | None = None
    on_lost: str | None = None
    max_resumes: int | None = None

    def options(self) -> dict[str, object]:
        """What it sets, as the call options of those names."""
        return {
            name: value
            for name, value in (
                ("inquire_after", self.inquire_after),
                ("on_lost", self.on_lost),
                ("max_resumes", self.max_resumes),
            )
            if value is not None
        }


F = TypeVar("F", bound=Callable[..., object])


def policy(
    *,
    inquire_after: float | str | None = None,
    on_lost: str | None = None,
    max_resumes: int | None = None,
) -> Callable[[F], F]:
    """Say how this app's calls of a protocol method are to be treated.

    Args:
        inquire_after: After this long without any event from the task, ask its agent whether
            it is still on it, and again after every further such silence. Seconds, or a
            string like ``"5s"`` or ``"2m"``. ``0`` never asks.
        on_lost: What becomes of the task should its agent be lost while it runs: ``"FAIL"``
            (it ends lost, and the call raises), ``"RESUME"`` (a workflow goes on from what it
            recorded) or ``"RESTART"`` (an action declared idempotent is run again). The
            server refuses what the other app's implementation did not declare.
        max_resumes: How often the task may be sent again before it ends lost after all.

    Returns:
        A decorator that leaves the method as it is and attaches the policy to it.

    Raises:
        ValueError: If a value is not one this can mean.
    """
    if on_lost is not None and on_lost not in ON_LOST:
        raise ValueError(f"on_lost is one of {', '.join(ON_LOST)}, not {on_lost!r}.")
    if max_resumes is not None and (isinstance(max_resumes, bool) or max_resumes < 0):
        raise ValueError(f"max_resumes is a count, not {max_resumes!r}.")
    declared = CallPolicy(
        inquire_after=seconds(inquire_after) if inquire_after is not None else None,
        on_lost=on_lost,
        max_resumes=max_resumes,
    )

    def decorator(method: F) -> F:
        setattr(method, CALL_POLICY_ATTR, declared)
        return method

    return decorator


def get_call_policy(method: object) -> CallPolicy | None:
    """The :class:`CallPolicy` attached to ``method``, if any."""
    return getattr(method, CALL_POLICY_ATTR, None)


__all__ = ["CALL_POLICY_ATTR", "CallPolicy", "get_call_policy", "policy", "seconds"]
