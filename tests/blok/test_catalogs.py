"""The spec's catalogs (base@2 and the standard catalogs) and the Orkestrator's module."""

import pytest

from arkitekt_spec.blok.catalogs import (
    base_catalog,
    blok_view,
    catalog_files,
    default_view,
    orkestrator_module,
    standard_catalog,
    standard_catalogs,
)
from arkitekt_spec.declare.catalogs import CALLBACK_KIND, VALUE_KINDS


def test_base_is_the_language() -> None:
    names = {component.name for component in base_catalog().components}

    assert names == {"if", "else", "foreach", "match", "case", "default", "fragment", "suspense", "fallback", "error", "ask", "Html"}


def test_the_catalogs_are_versioned_the_module_not_and_their_names_unique() -> None:
    seen: dict[str, str] = {}
    assert orkestrator_module().version is None
    for catalog in (base_catalog(), *standard_catalogs(), orkestrator_module()):
        for component in catalog.components:
            assert component.name not in seen, f"{component.name} in {seen.get(component.name)} and {component.catalog}"
            seen[component.name] = str(component.catalog)
    assert seen["Button"] == "ui@1" and seen["Scene"] == "scene@1" and seen["AgentLogo"] == seen["ActionLogo"] == "logo@1" and seen["div"] == "html@1"
    assert seen["Chart"] == "shadcn@1" and seen["MikroScene"] == "orkestrator"


def test_html_keeps_only_containers_that_translate_to_native() -> None:
    html = {component.name: component for component in standard_catalog("html").components}

    assert set(html) == {"div", "p", "span"}
    assert html["div"].text_prop is None and html["p"].text_prop == html["span"].text_prop == "text"


def test_the_renames_of_the_design() -> None:
    view = default_view()

    for gone in ("Show", "At", "SVG", "Logo", "TaskAsk", "query", "pending", "ready", "empty", "when", "h1", "ul", "Fragment"):
        assert view.component(gone) is None, gone
    for new in ("Visible", "Anchor", "Html", "AgentLogo", "ActionLogo", "ask", "Toast", "suspense", "Link", "Image", "Code", "Transform"):
        assert view.component(new) is not None, new
    anchor = view.component("Anchor")
    assert anchor is not None and anchor.prop("dep") is not None


def test_every_entry_declares_its_events_and_text() -> None:
    for name, component in default_view().components.items():
        assert {prop.kind for prop in component.props} <= set(VALUE_KINDS), name
        for prop in component.props:
            if prop.kind == CALLBACK_KIND:
                assert prop.event is not None, f"{name}.{prop.key} declares no event"
        if component.text_prop is not None:
            assert component.prop(component.text_prop) is not None, name
    button = default_view().component("Button")
    assert button is not None and button.text_prop == "label"


def test_no_entry_declares_persist_or_data_attributes() -> None:
    """A component keeps its own state if it chooses to, and every one sets data-busy and
    data-stale: neither is declared."""
    from arkitekt_spec.blok.catalogs import catalog_text

    for file in catalog_files():
        import json

        for entry in json.loads(catalog_text(file))["components"]:
            assert "persist" not in entry and "data_attributes" not in entry, f"{file}: {entry['name']}"
    scene = default_view().component("Scene")
    assert scene is not None and scene.prop("persist") is not None, "a component may still offer persist as a prop"


def test_a_bind_prop_says_what_the_control_writes() -> None:
    view = default_view()
    kinds = {name: view.component(name).prop("bind").kind for name in ("Slider", "Switch", "Checkbox", "Input", "Select", "ToggleGroup")}  # type: ignore[union-attr]

    assert kinds == {"Slider": "FLOAT", "Switch": "BOOL", "Checkbox": "BOOL", "Input": "STRING", "Select": "STRING", "ToggleGroup": "ANY"}


def test_ui_has_a_time_that_ticks_inside() -> None:
    time = default_view().component("Time")

    assert time is not None and time.catalog == "ui@1" and not time.accepts_children
    assert {prop.key: (prop.kind, prop.required) for prop in time.props} == {
        "className": ("STRING", False), "format": ("STRING", False), "relative": ("BOOL", False), "value": ("STRING", True),
    }


def test_a_module_shadows_a_standard_component_and_wins() -> None:
    from arkitekt_spec.declare.catalogs import Catalog, ComponentSpec

    mine = ComponentSpec(name="Button", catalog="mine")
    view = blok_view([Catalog(name="mine", components=(mine,))])

    assert view.component("Button") == mine
    shadow = view.shadows["Button"]
    assert (shadow.catalog, shadow.module, shadow.winner) == ("ui@1", "mine", "mine")


def test_a_module_never_shadows_the_language() -> None:
    from arkitekt_spec.declare.catalogs import Catalog, ComponentSpec

    view = blok_view([Catalog(name="mine", components=(ComponentSpec(name="foreach", catalog="mine"),))])

    assert view.component("foreach") == base_catalog().components[[c.name for c in base_catalog().components].index("foreach")]
    assert view.shadows["foreach"].winner == "base@2"


def test_the_default_view_shadows_nothing() -> None:
    assert dict(default_view().shadows) == {}


def test_every_file_ships() -> None:
    assert catalog_files() == (
        "catalogs/base@2.json", "catalogs/ui@1.json", "catalogs/scene@1.json", "catalogs/logo@1.json",
        "catalogs/structure@1.json", "catalogs/html@1.json", "catalogs/shadcn@1.json", "modules/orkestrator.json",
    )


def test_failed_is_a_toast_slot_now_not_a_read_slot() -> None:
    failed = default_view().component("failed")

    assert failed is not None and failed.downstream_of == ("Toast",)


def test_descriptions_speak_v2() -> None:
    """A callback reads ``event.<field>``, an asset is ``{self.assets.<name>}``: no ``$event``, no ``@``."""
    for name, component in default_view().components.items():
        for prop in component.props:
            text = prop.description or ""
            assert "$event" not in text and '"@self' not in text, f"{name}.{prop.key}: {text}"


def test_shadcn_holds_the_bento_grid() -> None:
    grid = default_view().component("BentoGrid")
    item = default_view().component("BentoItem")
    assert grid is not None and item is not None
    assert grid.catalog == item.catalog == "shadcn@1"
    assert item.downstream_of == ("BentoGrid",)
    assert {"columns", "rowHeight"} <= {prop.key for prop in grid.props}
    assert {"cols", "rows", "priority", "show"} <= {prop.key for prop in item.props}


def test_enum_props_hold_their_values() -> None:
    button = default_view().component("Button")
    assert button is not None
    variant, label = button.prop("variant"), button.prop("label")

    assert variant is not None and variant.values is not None and "outline" in variant.values
    assert label is not None and label.values is None
    for catalog in (base_catalog(), *standard_catalogs(), orkestrator_module()):
        for component in catalog.components:
            for prop in component.props:
                assert prop.values is None or (prop.kind == "STRING" and prop.values), f"{component.name}.{prop.key}"


def test_a_literal_outside_the_values_is_a_catalog_warning() -> None:
    from arkitekt_spec.blok.vocabulary import BlokError
    from arkitekt_spec.declare.blok.validate import validate_bsx

    warnings = validate_bsx('<Html><Button variant="loud">Go</Button></Html>')

    assert [warning.code for warning in warnings] == ["catalog.value"]
    assert validate_bsx('<Html><Button variant="outline">Go</Button></Html>') == []
    with pytest.raises(BlokError):
        validate_bsx('<Html><Button variant="loud">Go</Button></Html>', mode="strict")
