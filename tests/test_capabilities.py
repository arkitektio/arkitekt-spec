import pytest
from pydantic import ValidationError

from arkitekt_spec import (
    CAPABILITY_KINDS,
    AppManifest,
    ContainerEngineCapability,
    DeviceCapability,
    FolderCapability,
    Inspection,
    ReleaseDescriptor,
    ReleaseFlavour,
    capability_adapter,
    dump_descriptor,
    load_deployments,
    load_descriptor,
)

PINNED = "ghcr.io/org/engine@sha256:" + "b" * 64

EVERY_KIND = [
    {"kind": "container_engine", "name": "engine", "description": "Starts the apps it installs"},
    {"kind": "device", "name": "camera", "class": "video", "target": "/dev/video0"},
    {"kind": "folder", "name": "data", "target": "/data", "readOnly": False, "required": False},
    {"kind": "host_network", "name": "discovery"},
    {"kind": "linux_capability", "name": "realtime", "cap": "SYS_NICE"},
    {"kind": "shared_memory", "name": "shm", "min": 2048},
]


def flavour(capabilities: list[dict[str, object]]) -> ReleaseFlavour:
    return ReleaseFlavour.model_validate(
        {
            "name": "vanilla",
            "image": PINNED,
            "inspection": Inspection(),
            "capabilities": capabilities,
        }
    )


@pytest.mark.parametrize("raw", EVERY_KIND, ids=[c["kind"] for c in EVERY_KIND])
def test_every_kind_reads_and_writes_back_unchanged(raw: dict[str, object]):
    capability = capability_adapter().validate_python(raw)
    assert type(capability) is CAPABILITY_KINDS[str(raw["kind"])]
    assert (
        capability_adapter().dump_python(capability, mode="json", by_alias=True, exclude_unset=True)
        == raw
    )


def test_the_engine_socket_is_mounted_where_docker_clients_look_unless_said_otherwise():
    assert ContainerEngineCapability(name="engine").target == "/var/run/docker.sock"


def test_a_folder_is_read_only_and_a_capability_required_unless_said_otherwise():
    folder = FolderCapability(name="data", target="/data")
    assert folder.read_only is True
    assert folder.required is True


@pytest.mark.parametrize(
    "raw",
    [
        {
            "kind": "device",
            "name": "camera",
            "class": "video",
            "target": "/dev/video0",
            "host": "/dev/video2",
        },
        {"kind": "folder", "name": "data", "target": "/data", "read_only": False, "writable": True},
        {"kind": "privileged", "name": "everything"},
        {"kind": "device", "name": "camera", "class": "webcam", "target": "/dev/video0"},
        {"kind": "folder", "name": "Data Folder", "target": "/data"},
    ],
    ids=["a host path", "a typo", "privileged", "an unknown device class", "a name with spaces"],
)
def test_what_is_not_in_the_vocabulary_is_refused(raw: dict[str, object]):
    with pytest.raises(ValidationError):
        capability_adapter().validate_python(raw)


@pytest.mark.parametrize("cap", ["ALL", "SYS_ADMIN"])
def test_a_linux_capability_that_is_privileged_in_all_but_name_is_refused(cap: str):
    with pytest.raises(ValidationError, match="narrower"):
        capability_adapter().validate_python(
            {"kind": "linux_capability", "name": "root", "cap": cap}
        )


def test_two_capabilities_of_one_name_are_refused():
    with pytest.raises(ValidationError, match="Two capabilities are called `camera`"):
        flavour(
            [
                {"kind": "device", "name": "camera", "class": "video", "target": "/dev/video0"},
                {"kind": "device", "name": "camera", "class": "video", "target": "/dev/video1"},
            ]
        )


def test_a_descriptor_carries_its_flavours_capabilities_through_the_registry_bytes():
    release = ReleaseDescriptor(
        manifest=AppManifest(identifier="plugin_engine", version="1.0.0"),
        flavours=[flavour(EVERY_KIND)],
    )
    (only,) = load_descriptor(dump_descriptor(release)).flavours
    assert [type(c) for c in only.capabilities] == list(CAPABILITY_KINDS.values())
    assert only.capabilities[1] == DeviceCapability(
        name="camera", device_class="video", target="/dev/video0"
    )


def test_a_flavour_without_capabilities_is_written_as_before_they_existed():
    """The digest of a descriptor is its identity: one with nothing to grant keeps its bytes."""
    release = ReleaseDescriptor(
        manifest=AppManifest(identifier="starmist", version="0.1.0"), flavours=[flavour([])]
    )
    assert b"capabilities" not in dump_descriptor(release)


def test_an_app_image_in_a_deployments_file_carries_capabilities_too():
    file = load_deployments(
        {
            "app_images": [
                {
                    "appImageId": "1",
                    "manifest": {"identifier": "plugin_engine", "version": "1.0.0"},
                    "inspection": {},
                    "image": {"imageString": "arkitektio/plugin_engine:1.0.0"},
                    "capabilities": [{"kind": "container_engine", "name": "engine"}],
                }
            ]
        }
    )
    assert file.app_images[0].capabilities == [ContainerEngineCapability(name="engine")]
