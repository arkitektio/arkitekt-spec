"""The server version a client's release was tested against, carried in its wheel.

A client's release job runs its integration tests against a server; the stack fixture
asks that server what it is (:func:`record_tested`) and the answer is shipped in the
package as ``_tested.json``. The client then wants that server, as a requirement's
version (:func:`tested_against`)::

    TESTED = tested_against("mikro")

    def mikro(mikro: Annotated[Alias, Require("live.arkitekt.mikro", version=TESTED)]): ...

A checkout or an editable install has no stamp, and wants no version.

A release job checks the stamp before it builds (``python -m arkitekt_spec.declare.tested
mikro mikro.arkitekt:mikro_service``): there is one, and it is of the server major the
service declares its image for.
"""

import importlib
import json
import sys
import urllib.error
import urllib.request
from importlib import resources
from pathlib import Path

STAMP = "_tested.json"
"""The file in a package that says which server version its release was tested against."""

VERSION_HEADER = "Arkitekt-Service-Version"
"""Where a released service says its version, on every answer of its health route."""


def record_tested(package: str, health_url: str, *, timeout: float = 10.0) -> str | None:
    """Ask the server at ``health_url`` for its version and stamp it into ``package``.

    Returns the version; ``None``, with nothing written, when the server names none (a
    build that is no release) or does not answer there. Never raises: a test run does not
    fail over its stamp, a release without one does (:func:`refusal`).
    """
    try:
        with urllib.request.urlopen(health_url, timeout=timeout) as response:
            version = response.headers.get(VERSION_HEADER)
    except urllib.error.HTTPError as answered:
        # Unhealthy, it still says what it is: every answer of the route carries the header.
        version = answered.headers.get(VERSION_HEADER)
    except OSError:
        return None
    if not version:
        return None
    stamp = Path(str(resources.files(package))) / STAMP
    stamp.write_text(json.dumps({"version": version}) + "\n")
    return version


def tested_against(package: str) -> str | None:
    """What ``package`` wants of its server: the minor its release was tested on, in its major.

    ``">=22.7,<23"`` for a stamp of ``22.7.0``; ``None`` without a stamp, or with one that
    names no release.
    """
    try:
        stamp = resources.files(package) / STAMP
        version = json.loads(stamp.read_text())["version"]
        major, minor = (int(part) for part in str(version).split(".")[:2])
    except (OSError, ModuleNotFoundError, KeyError, ValueError):
        return None
    return f">={major}.{minor},<{major + 1}"


def stamped(package: str) -> str | None:
    """The version ``package`` is stamped with, as the server said it."""
    try:
        return str(json.loads((resources.files(package) / STAMP).read_text())["version"])
    except (OSError, ModuleNotFoundError, KeyError, ValueError):
        return None


def refusal(package: str, service: str | None = None) -> str | None:
    """Why ``package`` may not be released as it is; ``None`` when it may.

    ``service`` (``"module:attribute"``) is its declared service: when that names an image
    (``jhnnsrs/mikro:12``), the stamp has to be of that major.
    """
    version = stamped(package)
    if version is None or tested_against(package) is None:
        return f"{package} carries no server version it was tested against: its integration tests have to run, against a released server, before it is built."
    if service is not None:
        module, _, attribute = service.partition(":")
        image = getattr(getattr(importlib.import_module(module), attribute), "image", None)
        declared = str(image).rpartition(":")[2] if image else None
        if declared is not None and declared != version.split(".")[0]:
            return f"{package} declares {image}, and was tested against {version}: say the image it is written for."
    return None


if __name__ == "__main__":
    problem = refusal(*sys.argv[1:3])
    if problem:
        sys.exit(problem)
    print(f"{sys.argv[1]} was tested against {stamped(sys.argv[1])}: it wants {tested_against(sys.argv[1])}")
