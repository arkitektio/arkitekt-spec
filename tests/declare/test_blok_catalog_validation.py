"""Blok trees validated against a catalog at declaration time."""

import warnings

import pytest

from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.blok import bsx
from arkitekt_spec.declare.blok.registry import build_declared_bloks
from arkitekt_spec.declare.blok.validate import validate_blok_catalog
from arkitekt_spec.declare.catalogs import (
    HTML_CONTEXT,
    LOGO_CONTEXT,
    SCENE_CONTEXT,
    UNKNOWN_OPERATION,
    CatalogWarning,
    ComponentSpec,
    PropSpec,
    _parse_ui_catalog,
)

from .catalog_cases import BOX, CLAMP, FOREACH, SLIDER, base_view, electron_view


def _registry() -> AppRegistry:
    registry = AppRegistry()
    registry.declare_ui_catalog(
        "electron", components=[SLIDER, BOX], operations=[CLAMP]
    )
    return registry


def test_a_declared_catalog_refuses_an_unknown_component() -> None:
    registry = _registry()
    with pytest.raises(ValueError, match="is not registered in catalog"):
        registry.register_blok("bad", '<Box><Slidr min="1" /></Box>', catalog="electron")


def test_a_declared_catalog_refuses_a_missing_required_prop() -> None:
    registry = _registry()
    with pytest.raises(ValueError, match="requires props"):
        registry.register_blok("bad", "<Box><Slider /></Box>", catalog="electron")


def test_a_declared_catalog_refuses_children_on_a_leaf() -> None:
    registry = _registry()
    with pytest.raises(ValueError, match="does not accept children"):
        registry.register_blok(
            "bad", '<Box><Slider min="1"><Box /></Slider></Box>', catalog="electron"
        )


def test_an_unknown_operation_warns_but_registers() -> None:
    """UIs roll out operations independently of agents, so this must not block."""
    registry = _registry()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        registry.register_blok(
            "warns", '<Box><Slider min="@utils.nosuchop(1)" /></Box>', catalog="electron"
        )

    assert "warns" in registry.registered_bloks
    assert [w for w in caught if issubclass(w.category, CatalogWarning)]
    assert "nosuchop" in str(caught[0].message)


def test_a_known_operation_with_a_bad_argument_is_refused() -> None:
    registry = _registry()
    with pytest.raises(ValueError, match="does not accept arguments"):
        registry.register_blok(
            "bad", '<Box><Slider min="@utils.clamp(value=1, nope=2)" /></Box>', catalog="electron"
        )


def test_a_util_call_nested_in_an_agent_call_argument_is_checked() -> None:
    """The walker recurses through argument trees, so nesting hides nothing."""
    tree = bsx('<Box><Slider min="@dep.act(utils.clamp(value=1, nope=2))" /></Box>')
    with pytest.raises(ValueError, match="does not accept arguments"):
        validate_blok_catalog(tree, electron_view())


def test_without_a_declared_catalog_a_blok_is_checked_against_the_standard_one() -> None:
    """A blok that names no catalog is drawn by the standard one, whose components are known."""
    registry = AppRegistry()
    with pytest.raises(ValueError, match="'Slidr' is not registered"):
        registry.register_blok("anything", '<Slidr whatever="1" />')
    assert "anything" not in registry.registered_bloks


def test_naming_an_undeclared_catalog_falls_back_to_base_only() -> None:
    """A blok may name a catalog only the server knows; that is not an error here."""
    registry = AppRegistry()
    registry.register_blok("later", "<Slidr />", catalog="not-declared-here")
    assert registry.registered_bloks["later"].catalog == "not-declared-here"


def test_foreach_is_an_ordinary_component_to_a_catalog() -> None:
    """``foreach`` is BSX syntax, but the server has no concept of it either.

    It reaches the server as a component name like any other, so a catalog that has
    registered components must register ``foreach`` too. Exempting it here would make
    the client accept a tree the server rejects, which is the one thing a pre-flight
    check must never do.
    """
    registry = AppRegistry()
    registry.declare_ui_catalog("ui", components=[BOX])
    with pytest.raises(ValueError, match="'foreach' is not registered"):
        registry.register_blok("bad", '<Box><foreach let="#x" items="@d.s.xs" /></Box>', catalog="ui")


def test_foreach_passes_when_the_catalog_registers_it() -> None:
    registry = AppRegistry()
    registry.declare_ui_catalog("ui", components=[BOX, FOREACH])
    registry.register_blok("ok", '<Box><foreach let="#x" items="@d.s.xs" /></Box>', catalog="ui")
    assert "ok" in registry.registered_bloks


def test_structural_foreach_rules_still_fire_without_a_catalog() -> None:
    """The walker's own rules are unaffected by the catalog layer.

    The standard catalog asks nothing of a ``foreach``'s props, so what raises here is
    the walker's own requirement that a ``foreach`` carry ``items`` and ``let``.
    """
    registry = AppRegistry()
    with pytest.raises(ValueError, match="missing required prop"):
        registry.register_blok("bad", '<div><foreach let="#x" /></div>')


def test_the_catalog_reaches_the_wire_input() -> None:
    registry = _registry()
    registry.register_blok("panel", '<Box><Slider min="1" /></Box>', catalog="electron")
    registry.register_blok("plain", "<div />")

    bloks = build_declared_bloks(registry)
    assert bloks["panel"].catalog == "electron"
    assert bloks["plain"].catalog is None


def test_declaring_the_same_catalog_twice_differently_is_refused() -> None:
    registry = _registry()
    registry.declare_ui_catalog("electron", components=[SLIDER, BOX], operations=[CLAMP])
    with pytest.raises(ValueError, match="already declared"):
        registry.declare_ui_catalog("electron", components=[BOX])


def test_a_catalog_may_not_shadow_a_base_operation_at_declaration() -> None:
    from arkitekt_spec.declare.catalogs import OperationSpec

    registry = AppRegistry()
    with pytest.raises(ValueError, match="cannot redefine base operations"):
        registry.declare_ui_catalog(
            "rogue", operations=[OperationSpec(name="gt", returns="BOOL", arguments=())]
        )


def test_base_only_returns_no_findings_for_an_unknown_operation() -> None:
    tree = bsx('<Anything v="@utils.nosuchop(1)" />')
    assert validate_blok_catalog(tree, base_view()) == []


def test_findings_carry_the_unknown_operation_code() -> None:
    tree = bsx('<Box><Slider min="@utils.nosuchop(1)" /></Box>')
    findings = validate_blok_catalog(tree, electron_view())
    assert [f.code for f in findings] == [UNKNOWN_OPERATION]


def _scene_registry() -> AppRegistry:
    registry = AppRegistry()
    registry.declare_ui_catalog(
        "electron",
        components=[
            BOX,
            ComponentSpec(name="Label"),
            ComponentSpec(
                name="Scene",
                accepts_children=True,
                children_context=SCENE_CONTEXT,
                not_downstream_of=("Scene",),
            ),
            ComponentSpec(
                name="Model",
                accepts_children=True,
                context=SCENE_CONTEXT,
                downstream_of=("Scene",),
            ),
            ComponentSpec(name="Move", context=SCENE_CONTEXT, downstream_of=("Model", "Robot")),
            ComponentSpec(
                name="Popout",
                accepts_children=True,
                context=SCENE_CONTEXT,
                children_context=HTML_CONTEXT,
                downstream_of=("Model", "Robot"),
            ),
        ],
        operations=[],
    )
    return registry


def test_a_tree_that_bridges_into_a_scene_and_back_is_accepted() -> None:
    _scene_registry().register_blok(
        "view",
        "<Box><Scene><Model><Move /><Popout><Box><Label /></Box></Popout></Model></Scene></Box>",
        catalog="electron",
    )


@pytest.mark.parametrize(
    ("tree", "message"),
    [
        ("<Model />", "component 'Model' is a scene component and cannot be placed in the html context"),
        (
            "<Scene><Label /></Scene>",
            "component 'Label' is a html component and cannot be placed in the scene context",
        ),
        ("<Scene><Move /></Scene>", r"component 'Move' must be inside one of \['Model', 'Robot'\]"),
        (
            "<Scene><Model><Popout><Scene /></Popout></Model></Scene>",
            r"component 'Scene' must not be inside \['Scene'\]",
        ),
    ],
)
def test_a_component_is_held_to_its_declared_placement(tree: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _scene_registry().register_blok("bad", tree, catalog="electron")


def _logo_registry() -> AppRegistry:
    registry = AppRegistry()
    registry.declare_ui_catalog(
        "electron",
        components=[
            BOX,
            ComponentSpec(name="loop", accepts_children=True, inherits_context=True),
            ComponentSpec(
                name="SVG",
                accepts_children=True,
                props=(PropSpec(key="viewBox", kind="STRING", required=False),),
                children_context=LOGO_CONTEXT,
                not_downstream_of=("SVG",),
            ),
            ComponentSpec(name="G", accepts_children=True, context=LOGO_CONTEXT, downstream_of=("SVG",)),
            ComponentSpec(
                name="Path",
                props=(PropSpec(key="d", kind="STRING", required=True),),
                context=LOGO_CONTEXT,
                downstream_of=("SVG",),
            ),
            ComponentSpec(
                name="Scene", accepts_children=True, children_context=SCENE_CONTEXT, not_downstream_of=("Scene",)
            ),
            ComponentSpec(name="Model", context=SCENE_CONTEXT, downstream_of=("Scene",)),
        ],
        operations=[],
    )
    return registry


def test_a_logo_blok_is_one_svg_of_shapes() -> None:
    registry = _logo_registry()
    registry.register_blok(
        "logo",
        '<SVG viewBox="0 0 24 24"><G><Path d="M2 12h20" /></G></SVG>',
        catalog="electron",
        context="logo",
        default=True,
    )
    # An html blok may draw an SVG anywhere it likes.
    registry.register_blok("panel", '<Box><SVG><Path d="M0 0" /></SVG></Box>', catalog="electron")

    assert registry.registered_bloks["logo"].context == LOGO_CONTEXT
    assert registry.registered_bloks["logo"].default


@pytest.mark.parametrize(
    ("tree", "context", "message"),
    [
        ('<Path d="M0 0" />', "logo", "the root of a logo blok must be a 'SVG', not 'Path'"),
        ("<Box><SVG /></Box>", "logo", "the root of a logo blok must be a 'SVG', not 'Box'"),
        ('<loop><SVG /></loop>', "logo", "the root of a logo blok must be a 'SVG', not 'loop'"),
        ('<Box><Path d="M0 0" /></Box>', "html", "component 'Path' is a logo component and cannot be placed in the html context"),
        ("<SVG><loop><Box /></loop></SVG>", "logo", "component 'Box' is a html component and cannot be placed in the logo context"),
    ],
)
def test_a_logo_blok_is_held_to_its_root_and_its_shapes_to_the_svg(tree: str, context: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _logo_registry().register_blok("bad", tree, catalog="electron", context=context)


def test_control_flow_takes_the_context_it_is_placed_in() -> None:
    registry = _logo_registry()
    registry.register_blok("logo", '<SVG><loop><Path d="M0 0" /></loop></SVG>', catalog="electron", context="logo")
    registry.register_blok("scene", "<Scene><loop><Model /></loop></Scene>", catalog="electron", context="scene")
    registry.register_blok("panel", "<loop><Box /></loop>", catalog="electron")


def test_a_published_catalog_says_which_components_inherit_their_context() -> None:
    catalog = _parse_ui_catalog(
        {
            "name": "electron",
            "components": [
                {"name": "foreach", "acceptsChildren": True, "inheritsContext": True},
                {"name": "SVG", "acceptsChildren": True, "childrenContext": "LOGO"},
                {"name": "Path", "context": "LOGO"},
            ],
        }
    )
    components = {component.name: component for component in catalog.components}
    assert components["foreach"].inherits_context
    assert not components["Path"].inherits_context
    assert components["SVG"].children_context == LOGO_CONTEXT


def _logo(paths: int = 1, d: str = "M0 0") -> str:
    return "<SVG>" + "".join(f'<Path d="{d}" />' for _ in range(paths)) + "</SVG>"


@pytest.mark.parametrize(
    ("tree", "demo_state", "message"),
    [
        (_logo(paths=200), None, "a logo has at most 200 components, this one 201"),
        ("<SVG>" + "<G>" * 12 + "</G>" * 12 + "</SVG>", None, "a logo nests at most 12 deep"),
        (_logo(paths=2, d="M" + "1" * 8192), None, "at most 16384 characters of path data"),
        (
            '<SVG><foreach items="@self.lamps" let="#lamp"><Path d="M0 0" /></foreach></SVG>',
            {"self": {"lamps": list(range(65))}},
            "a foreach in a logo draws at most 64 items, its demo state has 65",
        ),
    ],
)
def test_a_logo_is_held_to_the_size_of_an_icon(tree: str, demo_state: dict | None, message: str) -> None:
    registry = _logo_registry()
    registry.declare_ui_catalog(
        "looping",
        components=[
            *registry.declared_catalogs["electron"].components,
            ComponentSpec(
                name="foreach",
                accepts_children=True,
                inherits_context=True,
                props=(PropSpec(key="items", kind="LIST", required=True), PropSpec(key="let", kind="STRING", required=False)),
            ),
        ],
        operations=[],
    )
    with pytest.raises(ValueError, match=message):
        registry.register_blok("big", tree, catalog="looping", context="logo", demo_state=demo_state)


def test_an_icon_sized_logo_passes_and_an_html_blok_has_no_such_limit() -> None:
    registry = _logo_registry()
    registry.register_blok("logo", _logo(paths=199), catalog="electron", context="logo")
    registry.register_blok("panel", "<Box>" + _logo(paths=300) + "</Box>", catalog="electron")


def _act(amount: float) -> float:
    """Return what it is given."""
    return amount


def test_an_action_is_drawn_by_a_logo_blok_the_app_declares() -> None:
    registry = _logo_registry()
    registry.register_blok("crop", _logo(), catalog="electron", context="logo", default=True)
    registry.register_action(_act, key="act", logo="crop")
    registry.register_action(_act, key="plain")

    agent = registry.to_implement_agent_input()

    logos = {implementation.definition.key: implementation.logo for implementation in agent.implementations or ()}
    assert logos == {"act": "crop", "plain": None}
    # An action without a logo sends no field at all: its wire bytes stay what they were.
    plain = next(i for i in agent.implementations or () if i.definition.key == "plain")
    assert "logo" not in plain.model_dump(exclude_unset=True)


def test_an_action_naming_no_declared_logo_is_refused() -> None:
    registry = _logo_registry()
    registry.register_blok("panel", "<Box />", catalog="electron")
    registry.register_action(_act, key="missing", logo="nowhere")
    registry.register_action(_act, key="not_a_logo", logo="panel")

    with pytest.raises(ValueError, match="'nowhere', which is not a logo blok this app declares"):
        registry.to_implement_agent_input()


def test_an_action_may_bring_its_own_logo(monkeypatch: pytest.MonkeyPatch) -> None:
    import arkitekt_spec.declare.app as app_module

    registry = _logo_registry()
    # An inline logo is drawn against the standard catalog; this one stands in for it.
    monkeypatch.setattr(app_module, "standard_view", lambda: registry.catalog_view("electron"))
    registry.register_action(_act, key="act", logo=bsx('<SVG><Path d="M1 1" /></SVG>'))

    agent = registry.to_implement_agent_input()

    (implementation,) = agent.implementations or ()
    assert implementation.logo == "act.logo"
    (blok,) = agent.bloks or ()
    assert (blok.key, blok.context) == ("act.logo", LOGO_CONTEXT)
