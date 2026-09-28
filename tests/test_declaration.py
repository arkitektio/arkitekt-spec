from pathlib import Path

import yaml

from arkitekt_spec import AppDeclaration, AppManifest, Requirement
from arkitekt_spec.actions import ImplementationInput

FIXTURES = Path(__file__).parent / "fixtures"
IMPLEMENTATIONS = [
    ImplementationInput.model_validate(v["implementation"])
    for v in yaml.safe_load((FIXTURES / "definitions.yaml").read_text())["definitions"][2:]
]


def declaration(description: str | None = "Segments nuclei") -> AppDeclaration:
    return AppDeclaration(
        manifest=AppManifest(identifier="starmist", version="0.1.0", description=description),
        requirements=[Requirement(key="mikro", service="live.arkitekt.mikro")],
        implementations=IMPLEMENTATIONS,
    )


def test_the_inspection_and_the_agent_input_say_the_same_thing():
    app = declaration()
    inspection = app.to_inspection(size=42)
    agent = app.to_agent_input()

    assert inspection.size == 42
    assert inspection.description == agent.description == "Segments nuclei"
    assert inspection.requirements == app.requirements
    assert list(inspection.implementations) == list(agent.implementations or ())


def test_an_undescribed_app_leaves_the_agent_description_unset():
    agent = declaration(description=None).to_agent_input()
    assert "description" not in agent.model_fields_set


def test_a_declaration_round_trips_as_json():
    app = declaration()
    assert AppDeclaration.model_validate_json(app.model_dump_json(by_alias=True)) == app
