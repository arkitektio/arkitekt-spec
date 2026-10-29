"""What a blok leaves to its tree, resolved the same way on every side (dependencies never are),
and the checks that come with Revision 3 (shadowed components, typed literals)."""

import warnings

import pytest
from pydantic import ValidationError

from arkitekt_spec.actions import AgentDependencyInput, BlokLocalInput, ComponentNodeInput
from arkitekt_spec.blok.bsx import parse_bsx
from arkitekt_spec.blok.catalogs import default_view
from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.blok.infer import resolve_scope
from arkitekt_spec.declare.blok.validate import BlokScope, check_blok_tree, dependency_hint
from arkitekt_spec.declare.catalogs import CatalogWarning, ComponentSpec, PropSpec
from arkitekt_spec.declare.traits.blok import CreateBlokInputTrait


class CreateBlok(CreateBlokInputTrait):
    """The fields of the server's ``CreateBlokInput`` the trait reads."""

    components: list[ComponentNodeInput] | None = None
    source: str | None = None
    dependencies: list[AgentDependencyInput] | None = None
    locals: list[BlokLocalInput] | None = None


def test_a_custom_blok_must_declare_the_dependencies_it_reads() -> None:
    with pytest.raises(ValidationError, match=r"'stage' is not a dependency of the blok .*Fix: dependencies=\[AgentDependencyInput\(key=\"stage\"\)\]"):
        CreateBlok(source="<Html><Button onClick={deps.stage.home()} /></Html>")


def test_a_declared_dependency_says_its_demands() -> None:
    robot = AgentDependencyInput(key="robot", state_dependencies=())
    with pytest.raises(ValidationError, match="deps.unknown_state"):
        CreateBlok(source="<Html><Text text={deps.robot.arm.mode} /></Html>", dependencies=[robot])


@pytest.mark.parametrize("kind", ["custom", "agent", "action", "task"])
def test_no_kind_infers_its_dependencies(kind: str) -> None:
    tree = parse_bsx("<Text text={deps.robot.x} />")
    scope = BlokScope(kind=kind, context="HTML")

    assert resolve_scope(tree, scope).dependencies == ()


def test_an_undeclared_dependency_hints_its_declaration() -> None:
    findings = check_blok_tree(parse_bsx("<Html><Text text={deps.laser.power} /></Html>"), BlokScope(kind="custom"))

    assert [finding.code for finding in findings] == ["deps.unknown"]
    assert 'dependencies=[AgentDependencyInput(key="laser")]' in findings[0].message


@pytest.mark.parametrize(
    ("kind", "source", "hint"),
    [
        ("custom", "<Html><Text text={deps.laser.power} /></Html>", 'dependencies=[AgentDependencyInput(key="laser")]'),
        ("task", "<Html><Text text={deps.laser.power} /></Html>", 'dependencies=[AgentDependencyInput(key="laser")]'),
        ("action", "<Html><Text text={deps.laser.power} /></Html>", "add a dependency parameter `laser` to the action"),
        ("action", "<Html><Button onClick={deps.laser.fire()} /></Html>", "add a dependency parameter `laser` to the action"),
    ],
)
def test_the_hint_says_what_the_kind_does_about_an_undeclared_dependency(kind: str, source: str, hint: str) -> None:
    findings = check_blok_tree(parse_bsx(source), BlokScope(kind=kind))

    assert [finding.code for finding in findings] == ["deps.unknown"]
    assert findings[0].message.endswith(f"Fix: {hint}.")


def test_an_agent_blok_reads_no_deps_at_all() -> None:
    """An agent blok has no ``deps`` root: the kind refuses it before any key is looked up, so
    its ``deps.unknown`` hint (``agent bloks read only `self```) is only the checker's fallback."""
    findings = check_blok_tree(parse_bsx("<Html><Text text={deps.laser.power} /></Html>"), BlokScope(kind="agent"))

    assert [finding.code for finding in findings] == ["roots.not_in_kind"]
    assert dependency_hint("laser", "agent") == "agent bloks read only `self`"


def test_resolving_twice_changes_nothing() -> None:
    tree = parse_bsx("<Card><Switch bind={on} /><Text text={args.x} /></Card>")
    once = resolve_scope(tree, BlokScope(kind="custom", locals=(BlokLocalInput(key="on"),)), default_view())

    assert resolve_scope(tree, once, default_view()) == once and once.locals[0].kind == "BOOL"


def test_a_string_on_a_typed_prop_says_how_to_write_it() -> None:
    (finding,) = validate_bsx_findings('<Progress value="50" />')
    assert finding.code == "props.literal_type" and finding.message.endswith("write value={50}.")
    (finding,) = validate_bsx_findings('<Button fullWidth="yes" />')
    assert finding.message.endswith("write fullWidth={true}.")


def validate_bsx_findings(source: str):
    return [finding for finding in check_blok_tree(parse_bsx(source), BlokScope(), default_view()) if finding.code == "props.literal_type"]


def test_a_module_that_shadows_a_standard_component_wins_with_a_warning() -> None:
    registry = AppRegistry()
    registry.declare_ui_catalog("lab", components=[ComponentSpec(name="Slider", props=(PropSpec(key="bind", kind="STRING", required=False),))])

    with pytest.warns(CatalogWarning, match=r"catalog.shadow"):
        registry.register_blok("panel", "<Html><Slider bind={label} /></Html>", module="lab", locals={"label": "x"})


def test_a_declared_module_draws_over_the_orkestrators() -> None:
    """Base, the standard catalogs, the Orkestrator's module, then the app's: as the server and the clients draw it."""
    registry = AppRegistry()
    registry.declare_ui_catalog("lab", components=[ComponentSpec(name="Gauge", text_prop=None)])
    view = registry.blok_view("lab")

    assert view.component("AspectRatio") is not None and view.component("Gauge") is not None
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        registry.register_blok("panel", "<Html><AspectRatio><Gauge /></AspectRatio></Html>", module="lab")


def test_an_enum_local_needs_its_choices() -> None:
    with pytest.raises(ValidationError, match="choices"):
        BlokLocalInput(key="mode", kind="ENUM")
    assert BlokLocalInput(key="mode", kind="ENUM", choices=[{"value": "fast", "label": "Fast"}]).kind == "ENUM"  # pyright: ignore[reportArgumentType]


def test_an_app_cannot_declare_a_module_over_the_language() -> None:
    with pytest.raises(ValueError, match="cannot redefine base components"):
        AppRegistry().declare_ui_catalog("lab", components=[ComponentSpec(name="if")])
