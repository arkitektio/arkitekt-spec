"""``.arkitekt/deployments.yaml``: the published images of an app, as a repo carries them."""

import datetime
from collections.abc import Mapping
from typing import Any, Literal

import yaml
from pydantic import Field

from arkitekt_spec.base import WireModel
from arkitekt_spec.inspection import Inspection
from arkitekt_spec.manifest import AppManifest
from arkitekt_spec.selectors import Selector

#: The path of the deployments file, relative to the repository root.
DEPLOYMENTS_PATH = ".arkitekt/deployments.yaml"

#: The current spec version. Bumped only for a change an older reader must refuse.
SPEC_VERSION: Literal[1] = 1


class DockerImage(WireModel):
    """Where a built image was pushed, and when it was built."""

    image_string: str = Field(alias="imageString", description="The pullable image reference.")
    build_at: datetime.datetime | None = Field(
        default=None, alias="buildAt", description="When the image was built."
    )


class AppImage(WireModel):
    """One published image of one flavour of one release."""

    app_image_id: str = Field(alias="appImageId", description="A unique id for this image.")
    flavour_name: str | None = Field(
        default=None, alias="flavourName", description="The flavour the image was built from."
    )
    manifest: AppManifest
    selectors: list[Selector] = Field(
        default_factory=list, description="Where the image may be placed."
    )
    inspection: Inspection
    image: DockerImage


class DeploymentsFile(WireModel):
    """The whole ``deployments.yaml``.

    Its top-level keys have always been snake_case; the keys inside an app image have
    always been camelCase. Both are kept as they are, so a file written before this
    spec existed reads unchanged.
    """

    spec_version: Literal[1] = Field(
        default=SPEC_VERSION, description="The version of this spec the file follows."
    )
    app_images: list[AppImage] = Field(default_factory=list)
    latest_app_image: str | None = None


def load_deployments(source: str | Mapping[str, Any] | None) -> DeploymentsFile:
    """Parse a deployments file from its YAML text or an already-parsed mapping."""
    data: Any = yaml.safe_load(source) if isinstance(source, str) else source
    if data is None:
        return DeploymentsFile()
    return DeploymentsFile.model_validate(data)


def dump_deployments(deployments: DeploymentsFile) -> str:
    """Render a deployments file the one way it is written to disk."""
    data: Any = deployments.model_dump(mode="json", by_alias=True, exclude_none=True)
    return yaml.safe_dump(data, sort_keys=True)
