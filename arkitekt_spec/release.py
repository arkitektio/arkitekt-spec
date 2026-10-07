"""A release as a registry carries it: the descriptor beside the images it describes.

A release of an app is published to one OCI repository and nowhere else. Its images
are pushed there, one multi-platform index per flavour, and the release itself is a
small JSON document, the *descriptor*, pushed to the same repository under the
release's own tag::

    ghcr.io/org/app:1.4.0                  the descriptor of release 1.4.0
    ghcr.io/org/app:main                   the descriptor the `main` channel points at
    ghcr.io/org/app:1.4.0-gpu              the image of its `gpu` flavour
    ghcr.io/org/app:1.4.1-dev.3f2a9c1-gpu  the image of a channel build

A descriptor names its images by digest, never by tag, so what it describes cannot
change under it. It is written last: a release exists once its descriptor does.

This module is the format only. It builds and reads the documents and never talks to
a registry: the plugin CLI pushes them and kabinet pulls them.
"""

import datetime
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, Literal, cast

from pydantic import Field, field_validator, model_validator

from arkitekt_spec.base import WireModel
from arkitekt_spec.inspection import Inspection
from arkitekt_spec.manifest import AppManifest
from arkitekt_spec.selectors import Selector

#: The current spec version. Bumped only for a change an older reader must refuse.
SPEC_VERSION: Literal[1] = 1

#: The media type of the descriptor. A manifest whose config has it is a release.
RELEASE_MEDIA_TYPE = "application/vnd.arkitekt.release.v1+json"

#: The media type of the manifest a descriptor is wrapped in.
OCI_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"

#: The registry a reference without a host belongs to.
DEFAULT_REGISTRY = "docker.io"

_TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
_UNTAGGABLE = re.compile(r"[^A-Za-z0-9._-]+")


class ReleaseFlavour(WireModel):
    """One flavour of a release: an image, and where it may be placed."""

    name: str = Field(description="The name of the flavour, unique within the release.")
    description: str | None = Field(default=None, description="What sets this flavour apart.")
    image: str = Field(
        description="The image, pinned by digest (`registry/repository@sha256:...`). "
        "For a multi-platform flavour this is the digest of the index."
    )
    platforms: list[str] = Field(
        default_factory=list, description="The platforms the image was built for."
    )
    selectors: list[Selector] = Field(
        default_factory=list, description="Where the image may be placed."
    )
    inspection: Inspection = Field(description="What the image declared when it was run.")
    built_at: datetime.datetime | None = Field(
        default=None, description="When the image was built."
    )

    @field_validator("image")
    @classmethod
    def _pinned(cls, value: str) -> str:
        if "@sha256:" not in value:
            raise ValueError(
                f"A flavour's image is named by digest, and '{value}' is not: "
                "a tag can be moved to another image after the release was inspected."
            )
        return value


class ReleaseDescriptor(WireModel):
    """One release of an app, as its registry repository carries it."""

    spec_version: Literal[1] = Field(
        default=SPEC_VERSION, description="The version of this spec the descriptor follows."
    )
    manifest: AppManifest
    channel: str | None = Field(
        default=None,
        description="The channel this build belongs to (a branch name). "
        "A release proper has none and never changes.",
    )
    revision: str | None = Field(
        default=None, description="The source revision the release was built from."
    )
    source: str | None = Field(
        default=None, description="Where the source lives. Informational only."
    )
    flavours: list[ReleaseFlavour] = Field(default_factory=list)

    @model_validator(mode="after")
    def _distinct_flavours(self) -> "ReleaseDescriptor":
        names = [flavour.name for flavour in self.flavours]
        repeated = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            raise ValueError(f"A release names each flavour once; repeated: {repeated}")
        return self


def dump_descriptor(descriptor: ReleaseDescriptor) -> bytes:
    """The bytes of a descriptor, the one way it is pushed.

    The digest of these bytes is the descriptor's identity, so the rendering is fixed:
    sorted keys, no insignificant whitespace.
    """
    data: Any = descriptor.model_dump(mode="json", by_alias=True, exclude_none=True)
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


def load_descriptor(source: bytes | str) -> ReleaseDescriptor:
    """Read a descriptor from the bytes a registry returned."""
    return ReleaseDescriptor.model_validate_json(source)


def digest_of(content: bytes) -> str:
    """The digest a registry addresses these bytes by."""
    return "sha256:" + hashlib.sha256(content).hexdigest()


def release_manifest(blob: bytes) -> dict[str, Any]:
    """The image manifest that carries a descriptor blob.

    The descriptor is the manifest's config, which is what marks it as a release. It is
    listed as the only layer too, since some registries refuse a manifest without one.
    Nothing here needs a registry newer than the first OCI spec.
    """
    blob_digest = digest_of(blob)
    reference = {"mediaType": RELEASE_MEDIA_TYPE, "digest": blob_digest, "size": len(blob)}
    return {
        "schemaVersion": 2,
        "mediaType": OCI_MANIFEST_MEDIA_TYPE,
        "config": reference,
        "layers": [reference],
    }


def descriptor_digest(manifest: object) -> str | None:
    """The digest of the descriptor a manifest carries, or None if it carries none.

    A repository holds images beside its releases; this is how a reader tells them apart.
    """
    if not isinstance(manifest, Mapping):
        return None
    config = cast("Mapping[str, object]", manifest).get("config")
    if not isinstance(config, Mapping):
        return None
    config = cast("Mapping[str, object]", config)
    digest = config.get("digest")
    if config.get("mediaType") != RELEASE_MEDIA_TYPE or not isinstance(digest, str):
        return None
    return digest


def parse_repository(reference: str) -> tuple[str, str]:
    """Split an OCI repository reference into its registry host and repository path.

    Follows the docker convention: the first component is a host only if it looks like
    one, and a single-component name on the default registry lives under `library/`.
    """
    reference = reference.strip().removeprefix("oci://").removeprefix("https://")
    if not reference or "@" in reference or ":" in reference.rsplit("/", 1)[-1]:
        raise ValueError(
            f"'{reference}' is not a repository: name the repository alone, "
            "without a tag or a digest."
        )
    head, _, rest = reference.partition("/")
    if rest and ("." in head or ":" in head or head == "localhost"):
        registry, repository = head, rest
    else:
        registry, repository = DEFAULT_REGISTRY, reference
        if "/" not in repository:
            repository = f"library/{repository}"
    if not repository or repository != repository.lower() or " " in repository:
        raise ValueError(f"'{repository}' is not a valid repository path (lowercase, no spaces).")
    return registry, repository


def channel_tag(channel: str) -> str:
    """The tag a channel's descriptor is pushed under: its name, made taggable."""
    return _checked(_UNTAGGABLE.sub("-", channel).strip("-."))


def release_tag(version: str) -> str:
    """The tag a release's descriptor is pushed under: its version."""
    return _checked(version)


def image_tag(version: str, flavour: str) -> str:
    """The tag that keeps a flavour's image of one build from being collected."""
    return _checked(f"{version}-{flavour}")


def channel_version(version: str, revision: str) -> str:
    """The version of a channel build: the declared version, marked with its revision."""
    return f"{version}-dev.{revision[:7]}"


def _checked(tag: str) -> str:
    if not _TAG.match(tag):
        raise ValueError(
            f"'{tag}' cannot be a registry tag: tags are at most 128 characters of "
            "letters, digits, '.', '_' and '-', and start with a letter, digit or '_'."
        )
    return tag
