"""An alias knows whether it is only reachable over the mesh, and never ships its proxy."""

from copy import deepcopy

import pytest

from arkitekt_spec.declare.wiring import Alias, MeshError, TurnInfo


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


class FakeNode:
    def __init__(self) -> None:
        self.forwards: list[tuple[str, int]] = []

    def __deepcopy__(self, memo: dict) -> "FakeNode":
        return self

    async def forward(self, host: str, port: int) -> str:
        self.forwards.append((host, port))
        return "127.0.0.1:5555"

    async def turn(self) -> TurnInfo:
        return TurnInfo(urls=["turn:127.0.0.1:3478?transport=udp"], username="u", credential="c")


@pytest.mark.asyncio
async def test_an_alias_reached_through_a_node_forwards_and_relays() -> None:
    node = FakeNode()
    cached = Alias(id="a", host="100.64.0.9", port=7880, kind="mesh")
    alias = cached.through_mesh("http://127.0.0.1:41234", node)

    assert alias.proxy == "http://127.0.0.1:41234"
    assert await alias.aforward() == "127.0.0.1:5555"
    assert await alias.aforward(443) == "127.0.0.1:5555"
    assert node.forwards == [("100.64.0.9", 7880), ("100.64.0.9", 443)]
    assert (await alias.aturn()).username == "u"
    # The alias it was resolved from is left alone.
    assert cached.proxy is None and cached._mesh is None


def test_the_node_is_never_serialized_and_copies_share_it() -> None:
    node = FakeNode()
    alias = Alias(id="a", host="db", kind="mesh").through_mesh("http://127.0.0.1:1", node)

    assert alias.model_dump() == Alias(id="a", host="db", kind="mesh").model_dump()
    assert alias.model_copy()._mesh is node
    assert alias.model_copy(deep=True)._mesh is node
    assert deepcopy(alias)._mesh is node


@pytest.mark.asyncio
async def test_forward_and_turn_need_a_node() -> None:
    plain = Alias(id="a", host="example.org")
    with pytest.raises(MeshError, match="not on the mesh"):
        await plain.aforward()
    with pytest.raises(MeshError, match="not on the mesh"):
        await plain.aturn()
    # Reached through an external proxy: no node of this process to ask.
    proxied = Alias(id="a", host="db", kind="mesh").through_mesh("http://127.0.0.1:1")
    with pytest.raises(MeshError, match="ARKITEKT_MESH_PROXY"):
        await proxied.aforward()
    with pytest.raises(MeshError, match="ARKITEKT_MESH_PROXY"):
        await proxied.aturn()
