"""The capability vocabulary: what a flavour needs *granted* on the machine it runs on.

Selectors (:mod:`arkitekt_spec.selectors`) say where a flavour may be placed. Capabilities
say what it must be given once it is: the container engine's socket, a device, a folder of
the host. A deployer places by selectors and grants capabilities, and a person installing
the app agrees to them first.

Semantics:

- A capability names the **container side only** — where the app expects the thing
  (``target``). The host side differs per machine (a rootless engine's socket, the camera
  that happened to come up as ``/dev/video2``, the folder the data lives in) and is the
  installer's to find or to ask for. A capability that named a host path would only work
  on the machine it was written on.
- ``name`` is what an operator answers to (``--device camera=/dev/video2``) and what a
  grant is recorded under; it is unique within a flavour.
- ``required: false`` marks a capability the app can run without: the person may decline
  it, and the deployer then leaves it out.
- A GPU is not a capability. The ``cuda``/``rocm``/``oneapi`` selectors already say a
  flavour needs one, and placing it on one implies the device reservation.
- There is deliberately no ``privileged``: whatever needs it names the narrower capability.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BaseCapability(BaseModel):
    """The fields every capability kind shares.

    Like selectors, capabilities REFUSE unknown keys: they are written by hand into a
    flavour's ``config.yaml``, where a typo must not silently change what is granted.
    """

    name: str = Field(
        pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$",
        description="What the capability is called within the flavour: the key an operator "
        "answers to, and what the grant is recorded under.",
    )
    description: str | None = Field(
        default=None,
        description="Why the app needs it, in a sentence: shown to the person who agrees to it.",
    )
    required: bool = Field(
        default=True,
        description="If false, the app runs without it and the person installing it may decline it.",
    )

    model_config = ConfigDict(extra="forbid", validate_by_name=True, validate_by_alias=True)


class ContainerEngineCapability(BaseCapability):
    """The container engine's API socket: the app starts and stops containers itself.

    Equivalent to root on the machine. The installer finds the socket — Docker's, a
    rootless engine's under ``$XDG_RUNTIME_DIR``, Podman's — and mounts it at ``target``.
    """

    kind: Literal["container_engine"] = "container_engine"
    target: str = Field(
        default="/var/run/docker.sock",
        description="Where the socket is mounted inside the container.",
    )


class DeviceCapability(BaseCapability):
    """A device of the host, passed through: a camera, a serial port, a USB device."""

    kind: Literal["device"] = "device"
    device_class: Literal["video", "serial", "usb", "dri", "other"] = Field(
        alias="class",
        description="What kind of device it is, so the installer can offer the matching ones.",
    )
    target: str = Field(
        description="Where the device appears inside the container, e.g. `/dev/video0`."
    )


class FolderCapability(BaseCapability):
    """A folder of the host, mounted into the container."""

    kind: Literal["folder"] = "folder"
    target: str = Field(description="Where the folder is mounted inside the container.")
    read_only: bool = Field(
        default=True,
        alias="readOnly",
        description="Whether the app only reads it. A writable folder is granted only on request.",
    )


class HostNetworkCapability(BaseCapability):
    """The host's network stack instead of the container's own: for discovery protocols
    and devices that are reached by broadcast."""

    kind: Literal["host_network"] = "host_network"


class LinuxCapability(BaseCapability):
    """One Linux capability added to the container (``cap_add``)."""

    kind: Literal["linux_capability"] = "linux_capability"
    cap: str = Field(
        pattern=r"^[A-Z][A-Z_]+$",
        description="The capability, without the `CAP_` prefix, e.g. `SYS_NICE`.",
    )

    @model_validator(mode="after")
    def _not_all(self) -> "LinuxCapability":
        if self.cap in {"ALL", "SYS_ADMIN"}:
            raise ValueError(
                f"`{self.cap}` is privileged in all but name: name the narrower capability "
                "the app needs"
            )
        return self


class SharedMemoryCapability(BaseCapability):
    """More shared memory (``/dev/shm``) than the engine's default of 64 MB."""

    kind: Literal["shared_memory"] = "shared_memory"
    min: int = Field(gt=0, description="The shared memory the app needs, in MB.")


Capability = Annotated[
    ContainerEngineCapability
    | DeviceCapability
    | FolderCapability
    | HostNetworkCapability
    | LinuxCapability
    | SharedMemoryCapability,
    Field(discriminator="kind"),
]
"""Any capability, dispatched on ``kind``."""

#: Every capability class, keyed by its ``kind``.
CAPABILITY_KINDS: dict[str, type[BaseCapability]] = {
    "container_engine": ContainerEngineCapability,
    "device": DeviceCapability,
    "folder": FolderCapability,
    "host_network": HostNetworkCapability,
    "linux_capability": LinuxCapability,
    "shared_memory": SharedMemoryCapability,
}


def unique_names(capabilities: list[BaseCapability]) -> list[BaseCapability]:
    """``capabilities``, refused if two share a name: a grant is recorded under it."""
    seen: set[str] = set()
    for capability in capabilities:
        if capability.name in seen:
            raise ValueError(f"Two capabilities are called `{capability.name}`")
        seen.add(capability.name)
    return capabilities
