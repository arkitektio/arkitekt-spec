"""Definition validators, effects and widgets checked against a catalog."""

import pytest

from arkitekt_spec.actions import (
    ActionKind,
    ArgPortInput,
    ComponentPropInput,
    CustomAssignWidgetInput,
    DefinitionInput,
    EffectKind,
    PortGroupInput,
    PortKind,
)
from arkitekt_spec.declare.catalogs import UNKNOWN_OPERATION
from arkitekt_spec.declare.definition.catalogs import (
    iter_definition_calls,
    iter_definition_widgets,
    validate_definition_against_catalog,
)
from arkitekt_spec.declare.widgets import withEffect, withValidator

from .catalog_cases import base_view, electron_view


def _definition(
    args: tuple = (),
    returns: tuple = (),
    port_groups: tuple = (),
    catalogs: tuple = (),
) -> DefinitionInput:
    return DefinitionInput(
        key="k",
        name="n",
        version="1",
        kind=ActionKind.FUNCTION,
        args=args,
        returns=returns,
        port_groups=port_groups,
        catalogs=catalogs,
    )


def _arg(key: str = "a", **kwargs: object) -> ArgPortInput:
    return ArgPortInput(key=key, kind=PortKind.INT, nullable=False, **kwargs)


def test_a_validator_call_is_checked() -> None:
    definition = _definition(args=(_arg(validators=(withValidator("value > 3"),)),))
    assert validate_definition_against_catalog(definition, electron_view()) == []


def test_an_unknown_operation_in_a_validator_warns() -> None:
    definition = _definition(
        args=(_arg(validators=(withValidator("nosuchop(value)"),)),)
    )
    findings = validate_definition_against_catalog(definition, electron_view())
    assert [f.code for f in findings] == [UNKNOWN_OPERATION]
    assert "validator of" in findings[0].message


def test_a_known_operation_with_a_bad_argument_is_refused() -> None:
    definition = _definition(
        args=(_arg(validators=(withValidator("clamp(value=value, nope=1)"),)),)
    )
    with pytest.raises(ValueError, match="does not accept arguments"):
        validate_definition_against_catalog(definition, electron_view())


def test_an_effect_call_is_checked() -> None:
    definition = _definition(
        args=(_arg(effects=(withEffect(EffectKind.HIDE, "nosuchop(value)"),)),)
    )
    findings = validate_definition_against_catalog(definition, electron_view())
    assert [f.code for f in findings] == [UNKNOWN_OPERATION]
    assert "effect of" in findings[0].message


def test_nested_ports_are_walked() -> None:
    child = ArgPortInput(
        key="c",
        kind=PortKind.INT,
        nullable=False,
        effects=(withEffect(EffectKind.HIDE, "nosuchop(value)"),),
    )
    definition = _definition(args=(_arg(children=(child,)),))
    assert len(validate_definition_against_catalog(definition, electron_view())) == 1


def test_port_group_effects_are_checked() -> None:
    definition = _definition(
        port_groups=(
            PortGroupInput(
                key="g", effects=(withEffect(EffectKind.HIDE, "nosuchop(value)"),)
            ),
        )
    )
    findings = validate_definition_against_catalog(definition, electron_view())
    assert [f.code for f in findings] == [UNKNOWN_OPERATION]
    assert "port group" in findings[0].message


def test_a_custom_widget_is_a_one_node_manifest() -> None:
    """Exactly the server's rule: the same component check as a blok node."""
    widget = CustomAssignWidgetInput(kind="CUSTOM", component="Slidr", props=())
    definition = _definition(args=(_arg(widget=widget),))
    with pytest.raises(ValueError, match="is not registered in catalog"):
        validate_definition_against_catalog(definition, electron_view())


def test_a_custom_widget_needs_its_required_props() -> None:
    widget = CustomAssignWidgetInput(kind="CUSTOM", component="Slider", props=())
    definition = _definition(args=(_arg(widget=widget),))
    with pytest.raises(ValueError, match="requires props"):
        validate_definition_against_catalog(definition, electron_view())


def test_a_valid_custom_widget_passes() -> None:
    widget = CustomAssignWidgetInput(
        kind="CUSTOM",
        component="Slider",
        props=(ComponentPropInput(key="min", static_value=0),),
    )
    definition = _definition(args=(_arg(widget=widget),))
    assert validate_definition_against_catalog(definition, electron_view()) == []


def test_widget_components_are_skipped_without_registered_components() -> None:
    widget = CustomAssignWidgetInput(kind="CUSTOM", component="Anything", props=())
    definition = _definition(args=(_arg(widget=widget),))
    assert validate_definition_against_catalog(definition, base_view()) == []


def test_iter_definition_calls_finds_every_call() -> None:
    definition = _definition(
        args=(
            _arg(
                "a",
                validators=(withValidator("value > 1"),),
                effects=(withEffect(EffectKind.HIDE, "value > 2"),),
            ),
        ),
        port_groups=(
            PortGroupInput(key="g", effects=(withEffect(EffectKind.HIDE, "value > 3"),)),
        ),
    )
    owners = [owner for owner, _ in iter_definition_calls(definition)]
    assert len(owners) == 3
    assert any("validator" in o for o in owners)
    assert any("port group" in o for o in owners)


def test_iter_definition_widgets_finds_nested_and_fallback_widgets() -> None:
    fallback = CustomAssignWidgetInput(kind="CUSTOM", component="Box", props=())
    widget = CustomAssignWidgetInput(
        kind="CUSTOM", component="Slider", props=(), fallback=fallback
    )
    definition = _definition(args=(_arg(widget=widget),))
    labels = [owner for owner, _ in iter_definition_widgets(definition)]
    assert len(labels) == 2
    assert any("fallback" in label for label in labels)


def test_a_util_call_nested_in_a_prop_agent_call_is_checked() -> None:
    """The server walks these; missing them would let a prop smuggle an operation past.

    A prop bound via ``agent_call`` carries util calls in its arguments. Checking only
    ``prop.util_call`` would make the client accept what the server rejects.
    """
    from arkitekt_spec.actions import (
        ActionArgumentInput,
        AgentProbeInput,
        UtilCallInput,
    )

    bad = UtilCallInput(
        operation="gt",
        arguments=(
            ActionArgumentInput(key="a", value_literal=1),
            ActionArgumentInput(key="nope", value_literal=2),
        ),
    )
    agent_call = AgentProbeInput(
        dependency="d",
        operation="op",
        arguments=(ActionArgumentInput(key="q", util_call=bad),),
    )
    widget = CustomAssignWidgetInput(
        kind="CUSTOM",
        component="Slider",
        props=(
            ComponentPropInput(key="min", static_value=0),
            ComponentPropInput(key="onChange", agent_call=agent_call),
        ),
    )
    definition = _definition(args=(_arg(widget=widget),))
    with pytest.raises(ValueError, match="does not accept arguments"):
        validate_definition_against_catalog(definition, electron_view())


def test_prop_calls_are_checked_even_without_registered_components() -> None:
    """Operation arity is knowable base-only, so it is checked even when components are not."""
    from arkitekt_spec.actions import (
        ActionArgumentInput,
        UtilCallInput,
    )

    bad = UtilCallInput(
        operation="gt",
        arguments=(
            ActionArgumentInput(key="a", value_literal=1),
            ActionArgumentInput(key="nope", value_literal=2),
        ),
    )
    widget = CustomAssignWidgetInput(
        kind="CUSTOM",
        component="Anything",
        props=(ComponentPropInput(key="x", util_call=bad),),
    )
    definition = _definition(args=(_arg(widget=widget),))
    with pytest.raises(ValueError, match="does not accept arguments"):
        validate_definition_against_catalog(definition, base_view())


def test_a_fallback_chain_does_not_duplicate_findings() -> None:
    """Each widget in the chain is visited once, so a finding is reported once."""
    fallback = CustomAssignWidgetInput(
        kind="CUSTOM",
        component="Slider",
        props=(ComponentPropInput(key="min", static_value=0),),
    )
    widget = CustomAssignWidgetInput(
        kind="CUSTOM",
        component="Slider",
        props=(ComponentPropInput(key="min", static_value=0),),
        fallback=fallback,
    )
    definition = _definition(args=(_arg(widget=widget),))
    assert validate_definition_against_catalog(definition, electron_view()) == []
