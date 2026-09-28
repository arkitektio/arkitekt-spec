"""Derived ``locks``/``manipulates`` must not depend on the process hash seed.

They feed the definition (and its hash) and generated artefacts such as
``blok.json``; ``list(set(...))`` made their order vary between processes.
"""

from dataclasses import dataclass

from arkitekt_spec.declare.actors.actify import derive_implementation_details
from arkitekt_spec.declare.actors.types import RegisterConfig
from arkitekt_spec.declare.app import AppRegistry

# These states belong to a registry, as they would to an app. There is no
# process-wide one to fall into.
_REGISTRY = AppRegistry()


@_REGISTRY.state(required_locks=["zeta", "alpha", "mu"])
@dataclass
class First:
    value: int = 0


@_REGISTRY.state(required_locks=["beta", "alpha"])
@dataclass
class Second:
    value: int = 0


def touch_both(first: First, second: Second) -> None:
    """Write both states."""
    first.value += 1
    second.value += 1


def test_derived_locks_and_manipulates_are_sorted_and_deduplicated() -> None:
    details = derive_implementation_details(
        touch_both, RegisterConfig(auto_locks=True), _REGISTRY.structure_registry
    )
    assert details.locks == ["alpha", "beta", "mu", "zeta"]
    assert details.manipulates == ["First", "Second"]


def test_explicit_locks_keep_the_users_order() -> None:
    details = derive_implementation_details(
        touch_both,
        RegisterConfig(locks=["zeta", "alpha"], manipulates=["Second", "First"]),
        _REGISTRY.structure_registry,
    )
    assert details.locks == ["zeta", "alpha"]
    assert details.manipulates == ["Second", "First"]
