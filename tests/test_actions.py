from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from arkitekt_spec import Inspection, definition_hash
from arkitekt_spec.actions import DefinitionInput, ImplementationInput

FIXTURES = Path(__file__).parent / "fixtures"
VECTORS = yaml.safe_load((FIXTURES / "definitions.yaml").read_text())["definitions"]


@pytest.mark.parametrize("vector", VECTORS, ids=[v["source"] for v in VECTORS])
def test_definition_hash_equals_the_servers(vector):
    """Digests computed once by the kabinet server's rekuest_core `unique_hash`."""
    implementation = ImplementationInput.model_validate(vector["implementation"])
    assert definition_hash(implementation.definition) == vector["server_hash"]


def test_an_explicit_null_means_the_default():
    """Older producers wrote omitted fields as null; the server would reject them."""
    starmist = next(v for v in VECTORS if "starmist" in v["source"])
    raw = starmist["implementation"]
    assert raw["definition"]["pure"] is None and raw["effect"] is None

    implementation = ImplementationInput.model_validate(raw)

    assert implementation.definition.pure is False
    assert implementation.effect == "NONE"


def test_a_null_for_a_nullable_field_stays_null():
    starmist = next(v for v in VECTORS if "starmist" in v["source"])
    implementation = ImplementationInput.model_validate(starmist["implementation"])
    assert implementation.definition.args[0].label is None


def test_an_unknown_key_in_a_definition_is_refused():
    raw = dict(VECTORS[0]["implementation"])
    raw["definition"] = {**raw["definition"], "argz": []}
    with pytest.raises(ValidationError):
        ImplementationInput.model_validate(raw)


def test_the_hash_ignores_what_is_not_identity():
    raw = VECTORS[0]["implementation"]["definition"]
    a = DefinitionInput.model_validate(raw)
    b = DefinitionInput.model_validate({**raw, "idempotent": True})
    assert definition_hash(a) == definition_hash(b)


def test_an_inspection_types_its_entries():
    inspection = Inspection.model_validate(
        {"implementations": [VECTORS[0]["implementation"]], "somethingNewer": 1}
    )
    assert isinstance(inspection.implementations[0], ImplementationInput)
