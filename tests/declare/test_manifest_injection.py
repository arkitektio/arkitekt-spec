"""A service or provider may ask for the app's manifest instead of the whole fakts.

The provider of an agent needs what the app says it is (name, version,
description) and nothing else fakts holds, so it takes an ``AppManifest`` -- a
run hands it its fakts' manifest, which is one (fakts' login manifest subclasses it).
"""

import pytest

from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.manifest import AppManifest


class LoginManifest(AppManifest):
    """Stands in for fakts' Manifest: the spec's plus runtime fields."""

    device_id: str | None = None


class Client:
    pass


class Agent:
    force: bool | None = None

    def __init__(self, manifest: AppManifest) -> None:
        self.manifest = manifest

    async def aprovide(self, context: object) -> None: ...
    async def aconnect(
        self, context: object = None, timeout: float | None = None
    ) -> None: ...
    async def aloop(self) -> None: ...


MANIFEST = LoginManifest(identifier="app", version="1.0", description="does things")


class Fakts:
    """A run's fakts that only know the manifest: nothing here resolves an alias."""

    def __init__(self, manifest: AppManifest) -> None:
        self.manifest = manifest

    async def aget_alias(self, fakts_key: str, /) -> object:
        raise AssertionError(f"no alias is asked for here ({fakts_key})")

    async def aget_alias_or_none(self, fakts_key: str, /) -> object:
        raise AssertionError(f"no alias is asked for here ({fakts_key})")

    async def aget_self_alias(self) -> object:
        raise AssertionError("no alias is asked for here")


FAKTS = Fakts(MANIFEST)


@pytest.mark.asyncio
async def test_a_service_is_handed_the_manifest() -> None:
    registry = AppRegistry()

    @registry.service()
    def thing(manifest: AppManifest) -> Client:
        """Knows which app it serves."""
        client = Client()
        client.manifest = manifest  # type: ignore[attr-defined]
        return client

    assert thing.needs_fakts
    assert thing.get_requirements() == []
    built = await thing.build(FAKTS, registry)
    assert built.manifest is MANIFEST  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_a_provider_is_handed_the_manifest() -> None:
    registry = AppRegistry()

    @registry.provider()
    def agent(manifest: AppManifest) -> Agent:
        """Registers under the app's name."""
        return Agent(manifest)

    assert agent.needs_fakts
    built = await agent.build(FAKTS, registry, {})
    assert built.manifest is MANIFEST
    assert built.manifest.description == "does things"


@pytest.mark.asyncio
async def test_a_subclass_annotation_is_the_manifest_too() -> None:
    registry = AppRegistry()

    @registry.provider()
    def agent(manifest: LoginManifest) -> Agent:
        """Asks for the richer login manifest."""
        return Agent(manifest)

    assert (await agent.build(FAKTS, registry, {})).manifest is MANIFEST
