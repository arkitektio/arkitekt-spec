"""A binder or a provider may ask for the app's manifest instead of the whole fakts.

The provider of an agent needs what the app says it is and nothing else fakts holds,
so it takes an ``AppManifest`` -- or fakts' login manifest, which subclasses it.
"""

from arkitekt_spec.declare.provider import provider
from arkitekt_spec.manifest import AppManifest


class LoginManifest(AppManifest):
    """Stands in for fakts' Manifest: the spec's plus runtime fields."""

    device_id: str | None = None


class Agent:
    pass


def test_a_provider_asks_for_the_manifest_by_annotation() -> None:
    @provider()
    def agent(manifest: LoginManifest) -> Agent:
        """Registers under the app's name."""
        return Agent()

    assert [(i.name, i.kind) for i in agent.injections] == [("manifest", "manifest")]
    assert agent.needs_fakts
    assert agent.get_requirements() == []
