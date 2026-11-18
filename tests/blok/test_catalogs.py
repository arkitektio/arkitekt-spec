"""The spec's catalogs: the base catalog every renderer draws, and the Orkestrator's on top of it."""

import json
from collections.abc import Iterator

import pytest

from arkitekt_spec.blok.catalogs import (
    ORKESTRATOR_CATALOG_ID,
    base_catalog,
    blok_view,
    catalog_chain,
    catalog_files,
    catalog_text,
    default_view,
    forget_catalogs,
    is_catalog_target,
    known_catalog,
    orkestrator_catalog,
    orkestrator_view,
    parse_catalog,
    register_catalog,
)
from arkitekt_spec.blok.vocabulary import BlokError, load_vocabulary
from arkitekt_spec.declare.catalogs import CALLBACK_KIND, VALUE_KINDS, Catalog, ComponentSpec

BASE_ID = "https://arkitekt.live/catalogs/base/v4"
ACME_ID = "https://acme.example/catalogs/lab/v1"


@pytest.fixture(autouse=True)
def _no_registered_catalogs() -> Iterator[None]:
    forget_catalogs()
    yield
    forget_catalogs()


def _acme(**overrides: object) -> str:
    payload: dict[str, object] = {
        "id": ACME_ID,
        "name": "lab",
        "version": 1,
        "minor": 2,
        "extends": ORKESTRATOR_CATALOG_ID,
        "components": [
            {"name": "Spectrum", "context": "PANEL", "props": [{"key": "trace", "kind": "LIST"}, {"key": "bands", "kind": "LIST", "since": 2}]},
            {"name": "Plate", "context": "PANEL", "since": 1, "props": []},
        ],
    }
    payload.update(overrides)
    return json.dumps(payload)


# --------------------------------------------------------------------------- the base catalog


def test_the_base_catalog_is_one_list_named_by_its_address() -> None:
    base = base_catalog()

    assert (base.id, base.name, base.version, base.minor, base.extends) == (BASE_ID, "base", 4, 2, None)
    assert load_vocabulary().base_catalog == BASE_ID
    names = [component.name for component in base.components]
    assert len(names) == len(set(names))


def test_the_base_catalog_holds_every_family() -> None:
    groups: dict[str, set[str]] = {}
    for component in base_catalog().components:
        groups.setdefault(str(component.group), set()).add(component.name)

    assert set(groups) == {"language", "ui", "html", "shadcn", "structure", "run", "scene", "logo"}
    assert groups["language"] == {"if", "else", "foreach", "match", "case", "default", "fragment", "suspense", "fallback", "error", "Blok", "local", "query", "subscription", "var", "#text"}
    assert groups["html"] == {"div", "p", "span"}
    assert groups["run"] == {"args", "arg", "deps", "dep", "ask", "returns", "status", "timeline", "label", "description", "message", "control", "field"}
    assert {"Button", "List", "Time"} <= groups["ui"] and {"Chart", "Drawer", "BentoGrid", "Combobox", "DatePicker"} <= groups["shadcn"]
    assert {"Scene", "Space", "Placed", "Route"} <= groups["scene"] and "Logo" in groups["logo"]
    assert {"StructureDisplay", "StructurePicker", "StructureViewer"} == groups["structure"]


def test_the_host_parts_say_the_kinds_they_belong_to() -> None:
    view = default_view()
    kinds = {name: view.components[name].kinds for name in load_vocabulary().host_parts}

    assert kinds == {
        "args": ("assign",), "arg": ("assign",), "deps": ("assign",), "dep": ("assign",),
        "ask": ("task", "action"), "returns": ("task", "action"), "status": ("task", "action"),
        "timeline": ("agent", "custom", "task", "action"),
        "label": ("port",), "description": ("port",), "message": ("port",), "control": ("port",), "field": ("port",),
    }
    assert {name: view.components[name].kinds for name in ("Space", "Placed", "Route")} == dict.fromkeys(("Space", "Placed", "Route"), ("action", "assign"))
    assert view.components["Scene"].kinds == () and view.components["Button"].kinds == ()


def test_the_host_parts_take_what_narrows_them() -> None:
    view = default_view()

    def props(name: str) -> dict[str, tuple[str, bool]]:
        return {prop.key: (prop.kind, prop.required) for prop in view.components[name].props}

    assert props("args") == {"only": ("ANY", False), "omit": ("ANY", False), "given": ("STRING", False)}
    assert props("arg") == props("dep") == {"key": ("STRING", True)}
    assert props("returns") == {"only": ("ANY", False), "omit": ("ANY", False)}
    assert props("deps") == props("status") == props("timeline") == props("ask") == {}
    chrome = view.components["Blok"].prop("chrome")
    assert chrome is not None and chrome.values == ("auto", "none")
    given = view.components["args"].prop("given")
    assert given is not None and given.values == ("hide", "show")


def test_what_needs_a_pointer_says_how_it_is_drawn_without_one() -> None:
    view = default_view()

    for name in ("Menubar", "Resizable", "HoverCard", "ContextMenu", "Tooltip"):
        assert view.components[name].touch, name
    assert view.components["Button"].touch is None


def test_html_keeps_only_containers_that_translate_to_native() -> None:
    html = {component.name: component for component in base_catalog().components if component.group == "html"}

    assert html["div"].text_prop is None and html["p"].text_prop == html["span"].text_prop == "text"


def test_the_renames_of_the_design() -> None:
    view = default_view()

    for gone in ("Show", "At", "SVG", "Html", "AgentLogo", "ActionLogo", "TaskAsk", "pending", "ready", "empty", "when", "h1", "ul", "Fragment", "Overlay", "Anchor"):
        assert view.component(gone) is None, gone
    for new in ("Visible", "Space", "Placed", "Blok", "Logo", "local", "query", "ask", "Toast", "suspense", "Link", "Image", "Code", "Transform"):
        assert view.component(new) is not None, new
    placed = view.component("Placed")
    assert placed is not None and placed.prop("dep") is not None and placed.prop("matrix") is not None
    space = view.component("Space")
    assert space is not None and space.prop("room") is not None


def test_every_entry_declares_its_events_and_text() -> None:
    for name, component in orkestrator_view().components.items():
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
    for file in catalog_files():
        for entry in json.loads(catalog_text(file))["components"]:
            assert "persist" not in entry and "data_attributes" not in entry, f"{file}: {entry['name']}"
    scene = default_view().component("Scene")
    assert scene is not None and scene.prop("persist") is not None, "a component may still offer persist as a prop"


def test_a_bind_prop_says_what_the_control_writes() -> None:
    view = default_view()
    kinds = {name: view.component(name).prop("bind").kind for name in ("Slider", "Switch", "Checkbox", "Input", "Select", "ToggleGroup")}  # type: ignore[union-attr]

    assert kinds == {"Slider": "FLOAT", "Switch": "BOOL", "Checkbox": "BOOL", "Input": "STRING", "Select": "STRING", "ToggleGroup": "ANY"}


def test_the_base_has_a_time_that_ticks_inside() -> None:
    time = default_view().component("Time")

    assert time is not None and time.catalog == "base@4" and not time.accepts_children
    assert {prop.key: (prop.kind, prop.required) for prop in time.props} == {
        "className": ("STRING", False), "format": ("STRING", False), "relative": ("BOOL", False), "value": ("STRING", True),
    }


def test_a_list_draws_a_foreach_row_by_row() -> None:
    long_list = default_view().component("List")

    assert long_list is not None and long_list.accepts_children and long_list.group == "ui"


def test_every_file_ships() -> None:
    assert catalog_files() == ("catalogs/base@4.json", "catalogs/orkestrator@1.json")


def test_failed_is_a_toast_slot_now_not_a_read_slot() -> None:
    failed = default_view().component("failed")

    assert failed is not None and failed.downstream_of == ("Toast",)


def test_descriptions_speak_v2() -> None:
    """A callback reads ``event.<field>``, an asset is ``{self.assets.<name>}``: no ``$event``, no ``@``."""
    for name, component in orkestrator_view().components.items():
        for prop in component.props:
            text = prop.description or ""
            assert "$event" not in text and '"@self' not in text, f"{name}.{prop.key}: {text}"


def test_enum_props_hold_their_values() -> None:
    button = default_view().component("Button")
    assert button is not None
    variant, label = button.prop("variant"), button.prop("label")

    assert variant is not None and variant.values is not None and "outline" in variant.values
    assert label is not None and label.values is None
    for component in orkestrator_view().components.values():
        for prop in component.props:
            assert prop.values is None or (prop.kind == "STRING" and prop.values), f"{component.name}.{prop.key}"


# --------------------------------------------------------------------------- an app catalog


def test_the_orkestrators_catalog_extends_the_base() -> None:
    orkestrator = orkestrator_catalog()

    assert (orkestrator.id, orkestrator.name, orkestrator.version, orkestrator.extends) == (ORKESTRATOR_CATALOG_ID, "orkestrator", 1, BASE_ID)
    assert [catalog.name for catalog in catalog_chain(ORKESTRATOR_CATALOG_ID)] == ["base", "orkestrator"]
    scene = orkestrator_view().component("MikroScene")
    assert scene is not None and scene.service == "mikro" and scene.catalog == "orkestrator@1"
    assert {component.service for component in orkestrator.components} == {"mikro"}


def test_a_view_is_of_one_catalog_and_knows_what_is_anothers() -> None:
    base, orkestrator = default_view(), orkestrator_view()

    assert base.target == BASE_ID and base.label == "base@4"
    assert base.component("MikroScene") is None and base.foreign["MikroScene"] == ORKESTRATOR_CATALOG_ID
    assert orkestrator.target == ORKESTRATOR_CATALOG_ID and orkestrator.label == "base@4 + orkestrator@1"
    assert orkestrator.component("MikroScene") is not None and orkestrator.component("Button") is not None
    assert dict(orkestrator.foreign) == {}


def test_a_published_catalog_is_registered_and_chains_down_to_the_base() -> None:
    assert known_catalog(ACME_ID) is None
    with pytest.raises(ValueError, match="not known here"):
        blok_view(ACME_ID)

    register_catalog(parse_catalog(_acme()))

    assert [catalog.name for catalog in catalog_chain(ACME_ID)] == ["base", "orkestrator", "lab"]
    view = blok_view(ACME_ID)
    assert view.component("Spectrum") is not None and view.component("MikroScene") is not None and view.component("Button") is not None
    assert default_view().foreign["Spectrum"] == ACME_ID


def test_a_catalog_only_adds() -> None:
    redefining = _acme(components=[{"name": "Button", "context": "PANEL", "props": []}])

    with pytest.raises(ValueError, match="redefines <Button> of base@4"):
        register_catalog(parse_catalog(redefining))
    assert known_catalog(ACME_ID) is None
    with pytest.raises(ValueError, match="redefines <foreach>"):
        blok_view(Catalog(name="mine", components=(ComponentSpec(name="foreach"),)))


def test_a_catalog_that_extends_one_not_known_is_refused() -> None:
    with pytest.raises(ValueError, match="which is not known here"):
        register_catalog(parse_catalog(_acme(extends="https://nowhere.example/catalogs/x/v9")))


def test_a_shipped_catalog_is_never_replaced() -> None:
    register_catalog(parse_catalog(json.dumps({"id": BASE_ID, "name": "base", "version": 4, "components": []})))

    assert default_view().component("Button") is not None


@pytest.mark.parametrize(
    "payload",
    [
        "[]",
        json.dumps({"components": []}),
        json.dumps({"name": "lab", "version": "1", "components": []}),
        json.dumps({"name": "lab", "id": "http://acme.example/catalogs/lab/v1", "components": []}),
        json.dumps({"name": "lab", "id": "https://acme.example/catalogs/lab", "components": []}),
        json.dumps({"name": "lab", "extends": "file:///etc/passwd", "components": []}),
        json.dumps({"name": "lab", "components": [{"name": "X", "props": [{"key": "a", "kind": "NOPE"}]}]}),
    ],
)
def test_a_payload_that_is_no_catalog_is_refused(payload: str) -> None:
    with pytest.raises(ValueError):
        parse_catalog(payload)


def test_what_can_name_a_catalog() -> None:
    assert is_catalog_target(ORKESTRATOR_CATALOG_ID) and is_catalog_target("mine")
    for wrong in ("ui@1", "http://arkitekt.live/catalogs/base/v4", "https://arkitekt.live/catalogs/base", "two words", ""):
        assert not is_catalog_target(wrong), wrong


# --------------------------------------------------------------------------- checked against one


def test_a_literal_outside_the_values_is_a_catalog_warning() -> None:
    from arkitekt_spec.declare.blok.validate import validate_bsx

    warnings = validate_bsx('<Blok kind="agent" context="panel"><Button variant="loud">Go</Button></Blok>')

    assert [warning.code for warning in warnings] == ["catalog.value"]
    assert validate_bsx('<Blok kind="agent" context="panel"><Button variant="outline">Go</Button></Blok>') == []
    with pytest.raises(BlokError):
        validate_bsx('<Blok kind="agent" context="panel"><Button variant="loud">Go</Button></Blok>', mode="strict")


def test_what_takes_text_is_the_catalogs_to_say() -> None:
    from arkitekt_spec.blok.bsx import parse_bsx
    from arkitekt_spec.declare.blok.validate import BlokScope, check_blok_tree, validate_bsx

    assert validate_bsx('<Blok kind="agent" context="panel"><Card>plain <Badge>new</Badge></Card><div>text</div><Button>Go</Button><if when={args.x}>shown</if></Blok>') == []
    findings = validate_bsx('<Blok kind="agent" context="panel"><Separator>x</Separator><Button label="a">b</Button></Blok>')
    assert [(finding.code, finding.fields["detail"]) for finding in findings] == [
        ("catalog.text", "takes no text"),
        ("catalog.text", "sets label as an attribute and as its text"),
    ]
    scene = parse_bsx('<Blok kind="agent" context="scene"><Scene><Model>x</Model></Scene></Blok>')
    assert [finding.code for finding in check_blok_tree(scene, BlokScope(), default_view()) if finding.path and "#" in finding.path] == ["catalog.placement"]
    assert check_blok_tree(scene, BlokScope(), None) == [], "the language alone takes text anywhere"


def test_a_component_of_another_catalog_is_refused_and_says_which() -> None:
    from arkitekt_spec.declare.blok.validate import validate_bsx

    with pytest.raises(BlokError) as refused:
        validate_bsx('<Blok kind="agent" context="panel"><MikroSceneProbe /></Blok>')

    assert refused.value.code == "catalog.target"
    assert ORKESTRATOR_CATALOG_ID in str(refused.value) and BASE_ID in str(refused.value)
    assert validate_bsx('<Blok kind="agent" context="panel"><MikroScene value={args.scene}><MikroSceneProbe /></MikroScene></Blok>', view=orkestrator_view()) == []


def test_the_minor_a_blok_needs_is_the_newest_it_uses() -> None:
    from arkitekt_spec.blok.bsx import parse_bsx
    from arkitekt_spec.declare.blok.registry import minor_needed

    register_catalog(parse_catalog(_acme()))
    view = blok_view(ACME_ID)

    def needed(source: str) -> int:
        return minor_needed(parse_bsx(source, view=view), view)

    assert needed('<Blok kind="agent" context="panel"><Button>Go</Button></Blok>') == 0
    assert needed('<Blok kind="agent" context="panel"><Spectrum trace={args.trace} /></Blok>') == 0
    assert needed('<Blok kind="agent" context="panel"><Plate /></Blok>') == 1
    assert needed('<Blok kind="agent" context="panel"><Plate /><Spectrum trace={args.trace} bands={args.bands} /></Blok>') == 2
