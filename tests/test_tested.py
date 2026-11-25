"""The server version a client's release was tested against, read back from its package."""

import json
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from arkitekt_spec.declare import tested
from arkitekt_spec.declare.tested import STAMP

NAME = "stampedclient"


@pytest.fixture
def package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """An importable package of its own, as an installed client is."""
    folder = tmp_path / NAME
    folder.mkdir()
    (folder / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    yield folder
    # Imported once, a name stays that folder: the next test has a folder of its own.
    sys.modules.pop(NAME, None)


def test_a_stamped_package_wants_the_minor_it_was_tested_on_within_its_major(
    package: Path,
) -> None:
    (package / STAMP).write_text(json.dumps({"version": "22.7.3"}))
    assert tested.tested_against(NAME) == ">=22.7,<23"


def test_a_package_without_a_stamp_or_with_no_release_in_it_wants_nothing(
    package: Path,
) -> None:
    assert tested.tested_against(NAME) is None
    (package / STAMP).write_text(json.dumps({"version": "0+local"}))
    assert tested.tested_against(NAME) is None
    assert tested.tested_against("no_such_package_anywhere") is None


def test_the_version_a_server_names_is_stamped_and_none_is_not(package: Path) -> None:
    said: list[str | None] = ["9.0.0"]

    class Health(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            if said[0]:
                self.send_header(tested.VERSION_HEADER, said[0])
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/ht"
        assert tested.record_tested(NAME, url) == "9.0.0"
        assert tested.tested_against(NAME) == ">=9.0,<10"

        # A build that is no release names no version: the stamp is left as it is.
        said[0] = None
        assert tested.record_tested(NAME, url) is None
        assert json.loads((package / STAMP).read_text()) == {"version": "9.0.0"}
    finally:
        server.shutdown()


def test_a_release_is_refused_without_a_stamp_or_with_one_of_another_major(
    package: Path,
) -> None:
    (package / "__init__.py").write_text(
        "from arkitekt_spec.declare.client import client\n"
        "@client(requires={}, image='jhnnsrs/stamped:22')\n"
        "class Stamped:\n    pass\n"
    )
    assert "no server version" in (tested.refusal(NAME) or "")

    (package / STAMP).write_text(json.dumps({"version": "23.0.1"}))
    assert tested.refusal(NAME) is None
    assert "declares jhnnsrs/stamped:22, and was tested against 23.0.1" in (
        tested.refusal(NAME, f"{NAME}:Stamped") or ""
    )

    (package / STAMP).write_text(json.dumps({"version": "22.7.0"}))
    assert tested.refusal(NAME, f"{NAME}:Stamped") is None
