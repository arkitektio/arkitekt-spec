"""The wire format of an Arkitekt app.

One definition of what an app is, shared by everything that produces or consumes it:
arkitekt's plugin CLI writes it, and the kabinet server reads it.

- :class:`AppDeclaration`: an app, declared once; every other shape is a projection of it.
- :class:`AppManifest` and :class:`Requirement`: who the app is, what services it needs.
- :class:`Inspection`: what the app declares, in the action language of :mod:`.actions`.
- :data:`Selector`: where a flavour of it may run.
- :class:`ReleaseDescriptor`: a release as its registry repository carries it (:mod:`.release`).
- :class:`DeploymentsFile`: ``.arkitekt/deployments.yaml``, the images a repo publishes.
"""

from typing import Any

from pydantic import TypeAdapter

from arkitekt_spec import actions
from arkitekt_spec.actions import definition_hash
from arkitekt_spec.declaration import AppDeclaration
from arkitekt_spec.deployment import (
    DEPLOYMENTS_PATH,
    SPEC_VERSION,
    AppImage,
    DeploymentsFile,
    DockerImage,
    dump_deployments,
    load_deployments,
)
from arkitekt_spec.inspection import Inspection
from arkitekt_spec.manifest import (
    DEFAULT_ENTRYPOINT,
    UNKNOWN_AUTHOR,
    AppManifest,
    Requirement,
)
from arkitekt_spec.release import (
    RELEASE_MEDIA_TYPE,
    ReleaseDescriptor,
    ReleaseFlavour,
    dump_descriptor,
    load_descriptor,
    parse_repository,
)
from arkitekt_spec.selectors import (
    SELECTOR_KINDS,
    BaseSelector,
    CpuSelector,
    CudaSelector,
    LabelSelector,
    OneApiSelector,
    RamSelector,
    RocmSelector,
    Selector,
)


def json_schema() -> dict[str, Any]:
    """The JSON Schema of ``deployments.yaml`` (and, within it, of every other model)."""
    return DeploymentsFile.model_json_schema(by_alias=True)


def release_json_schema() -> dict[str, Any]:
    """The JSON Schema of a release descriptor."""
    return ReleaseDescriptor.model_json_schema(by_alias=True)


def selector_adapter() -> TypeAdapter[Selector]:
    """A validator for a single selector, dispatched on ``kind``."""
    return TypeAdapter(Selector)


__all__ = [
    "DEFAULT_ENTRYPOINT",
    "DEPLOYMENTS_PATH",
    "RELEASE_MEDIA_TYPE",
    "SELECTOR_KINDS",
    "SPEC_VERSION",
    "UNKNOWN_AUTHOR",
    "AppDeclaration",
    "AppImage",
    "AppManifest",
    "BaseSelector",
    "CpuSelector",
    "CudaSelector",
    "DeploymentsFile",
    "DockerImage",
    "Inspection",
    "LabelSelector",
    "OneApiSelector",
    "RamSelector",
    "ReleaseDescriptor",
    "ReleaseFlavour",
    "Requirement",
    "RocmSelector",
    "Selector",
    "actions",
    "definition_hash",
    "dump_deployments",
    "dump_descriptor",
    "json_schema",
    "load_deployments",
    "load_descriptor",
    "parse_repository",
    "release_json_schema",
    "selector_adapter",
]
