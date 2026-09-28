"""Blok trees validated against a catalog at declaration time."""

import warnings

import pytest

from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.blok import bsx
from arkitekt_spec.declare.blok.registry import build_declared_bloks
from arkitekt_spec.declare.blok.validate import validate_blok_catalog
from arkitekt_spec.declare.catalogs import UNKNOWN_OPERATION, CatalogWarning

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


def test_without_a_declared_catalog_nothing_is_invented() -> None:
    """No catalog registered components, so component names are unknowable."""
    registry = AppRegistry()
    registry.register_blok("anything", '<Slidr whatever="1" />')
    assert "anything" in registry.registered_bloks


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

    With no catalog the component rules are skipped entirely, so what raises here is
    the walker's own requirement that a ``foreach`` carry ``items`` and ``let``.
    """
    registry = AppRegistry()
    with pytest.raises(ValueError, match="missing required prop"):
        registry.register_blok("bad", '<Box><foreach let="#x" /></Box>')


def test_the_catalog_reaches_the_wire_input() -> None:
    registry = _registry()
    registry.register_blok("panel", '<Box><Slider min="1" /></Box>', catalog="electron")
    registry.register_blok("plain", "<Box />")

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
