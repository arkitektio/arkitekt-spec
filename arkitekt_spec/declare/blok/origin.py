"""Where a blok was declared: its warnings point at the app's own line, and each is told once.

A blok is checked when it is declared (``app.blok(...)``, ``ActionBlok(...)`` in a decorator) and
again when the agent's input is built, against what the app declares by then (the states and
actions ``self`` reads, the services' schemas). Both checks report at the line that declared the
blok -- the first frame outside arkitekt and arkitekt-spec -- and the second only reports what
the first did not.
"""

import inspect
import warnings
from dataclasses import dataclass

from arkitekt_spec.blok.vocabulary import Finding
from arkitekt_spec.declare.catalogs import CatalogWarning

LIBRARIES = frozenset({"arkitekt_spec", "arkitekt"})
"""The packages a declaration passes through on its way in: never the line a warning names."""


@dataclass(frozen=True)
class DeclaredAt:
    """The app's line that declared a blok."""

    filename: str
    lineno: int
    """The line of the frame that declared it: for ``app.blok(...)`` the line the call starts on
    (CPython 3.11+ places a call there), which a BSX string spread over the lines below follows.
    A reader that wants the string finds it by its text, not by this line."""
    module: str


def declared_at() -> DeclaredAt | None:
    """The first frame of the call stack outside :data:`LIBRARIES`: the app's declaration."""
    frame = inspect.currentframe()
    while frame is not None:
        name = frame.f_globals.get("__name__")
        module = name if isinstance(name, str) else ""
        if module.split(".", 1)[0] not in LIBRARIES:
            return DeclaredAt(filename=frame.f_code.co_filename, lineno=frame.f_lineno, module=module)
        frame = frame.f_back
    return None


def finding_key(finding: Finding) -> str:
    """What makes two findings the same report: code, place and message."""
    return f"{finding.code}\x00{finding.path or ''}\x00{finding.message}"


def warn_at(message: str, at: DeclaredAt | None, *, finding: Finding | None = None, blok: str | None = None) -> None:
    """Warn ``message`` (a :class:`CatalogWarning`) at the app's line that declared the blok.

    The warning carries ``finding`` and ``blok``, so a reader that places it in the source
    (``arkitekt check --json-out``) need not parse the message back apart.
    """
    warning = CatalogWarning(message, finding=finding, blok=blok)
    if at is None:
        warnings.warn(warning, stacklevel=2)
        return
    warnings.warn_explicit(warning, CatalogWarning, at.filename, at.lineno, module=at.module)


__all__ = ["LIBRARIES", "DeclaredAt", "declared_at", "finding_key", "warn_at"]
