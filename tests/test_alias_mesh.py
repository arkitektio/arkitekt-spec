"""An alias knows whether it is only reachable over the mesh, and never ships its proxy."""

import pytest

from arkitekt_spec.declare.wiring import Alias


@pytest.mark.parametrize(
    ("kind", "host", "expected"),
    [
        ("mesh", "mikro.example.org", True),
        ("absolute", "100.64.0.9", False),  # the server's word wins over the address
        ("relative", "mikro", False),
        (None, "100.64.0.9", True),
        (None, "100.127.255.255", True),
        (None, "100.128.0.1", False),
        (None, "100.63.255.255", False),
        (None, "10.0.0.1", False),
        (None, "mikro.example.org", False),
        (None, "fd7a:115c:a1e0::1", False),
    ],
)
def test_is_mesh(kind: str | None, host: str, expected: bool) -> None:
    assert Alias(id="a", host=host, kind=kind).is_mesh() is expected


def test_proxy_is_never_serialized() -> None:
    alias = Alias(id="a", host="100.64.0.9", kind="mesh", proxy="http://127.0.0.1:41234")

    dumped = alias.model_dump()
    assert "proxy" not in dumped
    assert "proxy" not in alias.model_dump_json()
    assert dumped["kind"] == "mesh"
    assert Alias.model_validate(dumped).proxy is None


def test_an_unknown_kind_is_kept_and_not_mesh() -> None:
    alias = Alias.model_validate({"id": "a", "host": "100.64.0.9", "kind": "satellite"})

    assert alias.kind == "satellite"
    assert alias.is_mesh() is False
