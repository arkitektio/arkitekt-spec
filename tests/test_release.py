import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from arkitekt_spec import (
    AppManifest,
    CudaSelector,
    Inspection,
    ReleaseDescriptor,
    ReleaseFlavour,
    dump_descriptor,
    load_descriptor,
    parse_repository,
    release_json_schema,
)
from arkitekt_spec.release import (
    RELEASE_MEDIA_TYPE,
    channel_tag,
    channel_version,
    descriptor_digest,
    digest_of,
    image_tag,
    release_manifest,
    release_tag,
)

FIXTURES = Path(__file__).parent / "fixtures"
PINNED = "ghcr.io/org/starmist@sha256:" + "a" * 64


def flavour(name: str = "vanilla", image: str = PINNED) -> ReleaseFlavour:
    return ReleaseFlavour(name=name, image=image, inspection=Inspection())


def descriptor(**changes: object) -> ReleaseDescriptor:
    fields: dict[str, object] = {
        "manifest": AppManifest(identifier="starmist", version="0.1.0"),
        "flavours": [flavour()],
    }
    return ReleaseDescriptor.model_validate({**fields, **changes})


def test_golden_descriptor_is_in_canonical_form():
    """The golden file is exactly what the spec pushes: loading and dumping it is a no-op."""
    text = (FIXTURES / "release.json").read_bytes().strip()
    assert dump_descriptor(load_descriptor(text)) == text


def test_golden_descriptor_reads_every_field():
    release = load_descriptor((FIXTURES / "release.json").read_bytes())
    (only,) = release.flavours
    assert release.manifest.identifier == "starmist"
    assert release.channel is None
    assert release.revision == "3f2a9c1d"
    assert only.platforms == ["linux/amd64", "linux/arm64"]
    assert only.selectors == [CudaSelector(compute_capability="7.0")]
    assert only.inspection.implementations[0].definition.key == "predict_flou2"
    assert [r.key for r in only.inspection.requirements] == ["mikro", "rekuest"]


def test_a_flavour_refuses_an_image_named_by_tag():
    with pytest.raises(ValidationError, match="named by digest"):
        flavour(image="ghcr.io/org/starmist:0.1.0-vanilla")


def test_a_release_names_each_flavour_once():
    with pytest.raises(ValidationError, match="repeated"):
        descriptor(flavours=[flavour("gpu"), flavour("gpu")])


def test_an_unknown_descriptor_key_is_ignored_not_refused():
    data = json.loads(dump_descriptor(descriptor()))
    data["somethingNewer"] = 1
    assert load_descriptor(json.dumps(data)) == descriptor()


def test_the_same_release_always_has_the_same_bytes():
    one = descriptor(flavours=[flavour("a"), flavour("b")])
    again = load_descriptor(dump_descriptor(one))
    assert dump_descriptor(again) == dump_descriptor(one)


def test_a_release_manifest_is_recognised_by_its_config():
    blob = dump_descriptor(descriptor())
    manifest = release_manifest(blob)
    assert manifest["config"]["mediaType"] == RELEASE_MEDIA_TYPE
    assert manifest["layers"] == [manifest["config"]]
    assert descriptor_digest(manifest) == digest_of(blob)


@pytest.mark.parametrize(
    "manifest",
    [
        None,
        {"manifests": []},
        {"config": {"mediaType": "application/vnd.oci.image.config.v1+json", "digest": "x"}},
    ],
)
def test_an_image_is_not_taken_for_a_release(manifest: object):
    assert descriptor_digest(manifest) is None


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("ghcr.io/org/app", ("ghcr.io", "org/app")),
        ("oci://registry.gitlab.com/group/sub/app", ("registry.gitlab.com", "group/sub/app")),
        ("localhost:5000/app", ("localhost:5000", "app")),
        ("jhnnsrs/starmist", ("docker.io", "jhnnsrs/starmist")),
        ("starmist", ("docker.io", "library/starmist")),
    ],
)
def test_a_repository_reference_splits_like_docker_does(reference: str, expected: tuple[str, str]):
    assert parse_repository(reference) == expected


@pytest.mark.parametrize(
    "reference", ["ghcr.io/org/app:1.0.0", "ghcr.io/org/app@sha256:abc", "ghcr.io/Org/App", ""]
)
def test_a_repository_reference_refuses_anything_but_a_repository(reference: str):
    with pytest.raises(ValueError):
        parse_repository(reference)


def test_tags():
    assert release_tag("1.4.0") == "1.4.0"
    assert image_tag("1.4.0", "gpu") == "1.4.0-gpu"
    assert channel_tag("feat/new-thing") == "feat-new-thing"
    assert channel_version("1.4.0", "3f2a9c1d77") == "1.4.0-dev.3f2a9c1"
    assert release_tag(channel_version("1.4.0", "3f2a9c1d77"))


def test_a_version_that_cannot_be_a_tag_is_refused():
    with pytest.raises(ValueError, match="cannot be a registry tag"):
        release_tag("1.4.0+local")


def test_release_schema_matches_the_committed_snapshot():
    """A format change must show up in review: regenerate with `python -m arkitekt_spec release`."""
    committed = json.loads((FIXTURES / "release.schema.json").read_text())
    assert release_json_schema() == committed
