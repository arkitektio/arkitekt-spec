"""What a blok leaves to its tree, resolved the same way on every side (dependencies never are),
and the checks that come with Revision 3 (shadowed components, typed literals)."""

import warnings

import pytest
from pydantic import ValidationError

from arkitekt_spec.actions import AgentDependencyInput, BlokLocalInput, ComponentNodeInput
from arkitekt_spec.blok.bsx import parse_bsx
from arkitekt_spec.blok.catalogs import default_view
from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.blok.validate import BlokScope, check_blok_tree, dependency_hint
from arkitekt_spec.declare.catalogs import ComponentSpec, PropSpec
from arkitekt_spec.declare.traits.blok import CreateBlokInputTrait

CUSTOM = '<Blok kind="custom" context="panel">'


class CreateBlok(CreateBlokInputTrait):
    """The fields of the server's ``CreateBlokInput`` the trait reads."""

    components: list[ComponentNodeInput] | None = None
    source: str | None = None
    dependencies: list[AgentDependencyInput] | None = None


def test_a_custom_blok_must_declare_the_dependencies_it_reads() -> None:
    with pytest.raises(ValidationError, match=r"'stage' is not a dependency of the blok .*Fix: dependencies=\[AgentDependencyInput\(key=\"stage\"\)\]"):
        CreateBlok(source=f"{CUSTOM}<Button onClick={{agents.stage.home()}} /></Blok>")


def test_a_declared_dependency_says_its_demands() -> None:
    robot = AgentDependencyInput(key="robot", state_dependencies=())
    with pytest.raises(ValidationError, match="deps.unknown_state"):
        CreateBlok(source=f"{CUSTOM}<Text text={{agents.robot.arm.mode}} /></Blok>", dependencies=[robot])


@pytest.mark.parametrize(
    ("kind", "source", "hint"),
    [
        ("custom", "<Text text={agents.laser.power} />", 'dependencies=[AgentDependencyInput(key="laser")]'),
        ("task", "<Text text={deps.laser.power} />", 'dependencies=[AgentDependencyInput(key="laser")]'),
        ("action", "<Text text={deps.laser.power} />", "add a dependency parameter `laser` to the action"),
        ("action", "<Button onClick={deps.laser.fire()} />", "add a dependency parameter `laser` to the action"),
    ],
)
def test_the_hint_says_what_the_kind_does_about_an_undeclared_dependency(kind: str, source: str, hint: str) -> None:
    findings = check_blok_tree(parse_bsx(f'<Blok kind="{kind}" context="panel">{source}</Blok>'), BlokScope(dependencies=()))

    assert [finding.code for finding in findings] == ["deps.unknown"]
    assert findings[0].message.endswith(f"Fix: {hint}.")


def test_an_agent_blok_reads_only_the_dependencies_it_declares() -> None:
    """An agent blok's dependencies are in its own tree: one it does not declare is unknown
    wherever it is mounted, and the hint says how to declare it."""
    findings = check_blok_tree(parse_bsx('<Blok kind="agent" context="panel"><Text text={deps.laser.power} /></Blok>'), BlokScope(dependencies=()))

    assert [finding.code for finding in findings] == ["deps.unknown"]
    assert dependency_hint("laser", "agent") == 'declare it in the blok: <dependencies key="laser" like="self.<action>" />, then read laser.<dependency>'


def test_a_string_on_a_typed_prop_says_how_to_write_it() -> None:
    (finding,) = validate_bsx_findings('<Progress value="50" />')
    assert finding.code == "props.literal_type" and finding.message.endswith("write value={50}.")
    (finding,) = validate_bsx_findings('<Button fullWidth="yes" />')
    assert finding.message.endswith("write fullWidth={true}.")


def validate_bsx_findings(source: str):
    tree = parse_bsx(f'<Blok kind="agent" context="panel">{source}</Blok>')
    return [finding for finding in check_blok_tree(tree, BlokScope(), default_view()) if finding.code == "props.literal_type"]


def test_a_catalog_that_redefines_a_base_component_is_refused() -> None:
    registry = AppRegistry()

    with pytest.raises(ValueError, match="redefines <Slider> of base@4: a catalog only adds"):
        registry.declare_ui_catalog("lab", components=[ComponentSpec(name="Slider", props=(PropSpec(key="bind", kind="STRING", required=False),))])
    assert "lab" not in registry.declared_catalogs


def test_a_declared_catalog_adds_to_the_base() -> None:
    """The base catalog, then the app's own: a blok written for it draws both."""
    registry = AppRegistry()
    registry.declare_ui_catalog("lab", components=[ComponentSpec(name="Gauge", text_prop=None)])
    view = registry.blok_view("lab")

    assert view.component("AspectRatio") is not None and view.component("Gauge") is not None
    assert view.component("MikroScene") is None, "it does not extend the Orkestrator's"
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        registry.register_blok("panel", '<Blok kind="agent" context="panel" catalog="lab"><AspectRatio><Gauge /></AspectRatio></Blok>')


def test_an_enum_local_needs_its_choices() -> None:
    with pytest.raises(ValidationError, match="choices"):
        BlokLocalInput(key="mode", kind="ENUM")
    assert BlokLocalInput(key="mode", kind="ENUM", choices=[{"value": "fast", "label": "Fast"}]).kind == "ENUM"  # pyright: ignore[reportArgumentType]


def test_an_app_cannot_declare_a_module_over_the_language() -> None:
    with pytest.raises(ValueError, match="cannot redefine base components"):
        AppRegistry().declare_ui_catalog("lab", components=[ComponentSpec(name="if")])
