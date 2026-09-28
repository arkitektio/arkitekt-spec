import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from arkitekt_spec import (
    AppImage,
    CudaSelector,
    DeploymentsFile,
    Inspection,
    LabelSelector,
    dump_deployments,
    json_schema,
    load_deployments,
    selector_adapter,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_golden_file_is_in_canonical_form():
    """The golden file is exactly what the spec writes: loading and dumping it is a no-op."""
    text = (FIXTURES / "deployments.yaml").read_text()
    assert dump_deployments(load_deployments(text)) == text


def test_the_legacy_file_canonicalises_stably():
    once = dump_deployments(load_deployments((FIXTURES / "legacy_deployments.yaml").read_text()))
    assert dump_deployments(load_deployments(once)) == once


def test_golden_file_reads_every_envelope_field():
    file = load_deployments((FIXTURES / "deployments.yaml").read_text())
    (image,) = file.app_images
    assert image.manifest.description == image.inspection.description
    assert image.manifest.entrypoint == "app"
    assert image.inspection.size == 11834567890
    assert [r.key for r in image.inspection.requirements] == ["mikro", "rekuest"]
    assert image.selectors == [CudaSelector(compute_capability="7.0")]
    assert image.image.image_string == "jhnnsrs/starmist:0.1.0-vanilla"
    assert file.latest_app_image == image.app_image_id


def test_a_file_written_before_the_spec_still_loads():
    """The server's historic fixture: no spec_version, no description anywhere."""
    file = load_deployments((FIXTURES / "legacy_deployments.yaml").read_text())
    (image,) = file.app_images
    assert file.spec_version == 1
    assert image.manifest.identifier == "ome"
    assert image.manifest.description is None
    assert image.inspection.description is None
    assert image.inspection.implementations[0].definition.key == "convert_omero"
    assert image.selectors == []


def test_the_action_language_is_typed_and_reads_both_spellings():
    legacy = load_deployments((FIXTURES / "legacy_deployments.yaml").read_text())
    definition = legacy.app_images[0].inspection.implementations[0].definition
    assert definition.key == "convert_omero"
    assert definition.is_dev is False  # read from camelCase `isDev`


def test_an_unknown_envelope_key_is_ignored_not_refused():
    """A newer producer adds a field; an older reader must survive it."""
    inspection = Inspection.model_validate(
        {"description": "x", "somethingNewer": 1, "implementations": []}
    )
    assert inspection.description == "x"


def test_a_selector_refuses_an_unknown_key():
    with pytest.raises(ValidationError):
        selector_adapter().validate_python({"kind": "cuda", "cudaCorez": 3})


def test_a_selector_reads_both_spellings():
    adapter = selector_adapter()
    assert adapter.validate_python({"kind": "cuda", "cuda_cores": 3}) == adapter.validate_python(
        {"kind": "cuda", "cudaCores": 3}
    )


def test_a_label_selector_needs_its_key():
    with pytest.raises(ValidationError):
        LabelSelector.model_validate({"kind": "label"})


def test_empty_or_missing_file_is_an_empty_deployments_file():
    assert load_deployments(None) == DeploymentsFile()
    assert load_deployments("") == DeploymentsFile()


def test_plain_dump_stays_snake_case():
    """Consumers store model_dump() as JSON; the default must not flip casing."""
    image = load_deployments((FIXTURES / "deployments.yaml").read_text()).app_images[0]
    assert "compute_capability" in image.selectors[0].model_dump()
    assert "image_string" in AppImage.model_dump(image)["image"]


def test_json_schema_matches_the_committed_snapshot():
    """A format change must show up in review: regenerate with `python -m arkitekt_spec`."""
    committed = json.loads((FIXTURES / "deployments.schema.json").read_text())
    assert json_schema() == committed
