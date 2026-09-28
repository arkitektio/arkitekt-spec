"""The catalog rules, as a case table.

Every row states what the server would do for the same input; the parity test checks the
rule *inventory* against the server, this checks the behaviour.
"""

import pytest

from arkitekt_spec.declare.catalogs import (
    UNKNOWN_OPERATION,
    check_call,
    check_component,
    resolve_catalogs,
)

from .catalog_cases import (
    CALL_CASES,
    COMPONENT_CASES,
    ELECTRON,
    ERROR,
    OK,
    base_view,
    electron_view,
)


@pytest.mark.parametrize(
    ("operation", "keys", "authoritative", "expectation"),
    [case[1:] for case in CALL_CASES],
    ids=[case[0] for case in CALL_CASES],
)
def test_check_call(operation: str, keys: tuple, authoritative: bool, expectation: str) -> None:
    view = resolve_catalogs([ELECTRON], authoritative=authoritative)

    if expectation is ERROR:
        with pytest.raises(ValueError):
            check_call(operation, keys, view, "owner")
        return

    finding = check_call(operation, keys, view, "owner")
    if expectation is OK:
        assert finding is None
    else:
        assert finding is not None and finding.code == UNKNOWN_OPERATION


@pytest.mark.parametrize(
    ("component", "prop_keys", "has_children", "bindings", "expectation"),
    [case[1:] for case in COMPONENT_CASES],
    ids=[case[0] for case in COMPONENT_CASES],
)
def test_check_component(
    component: str, prop_keys: tuple, has_children: bool, bindings: dict, expectation: str
) -> None:
    view = electron_view()

    if expectation is ERROR:
        with pytest.raises(ValueError):
            check_component(component, prop_keys, has_children, bindings, view, "owner")
    else:
        check_component(component, prop_keys, has_children, bindings, view, "owner")


@pytest.mark.parametrize(
    ("component", "prop_keys", "has_children", "bindings", "expectation"),
    [case[1:] for case in COMPONENT_CASES],
    ids=[case[0] for case in COMPONENT_CASES],
)
def test_every_component_rule_is_skipped_without_specs(
    component: str, prop_keys: tuple, has_children: bool, bindings: dict, expectation: str
) -> None:
    """With no registered components nothing is knowable, so nothing is invented.

    This is the server's rule too: ``resolve_components`` returns ``None`` and every
    component check is skipped, so a blok against an empty catalog always passes.
    """
    check_component(component, prop_keys, has_children, bindings, base_view(), "owner")


def test_unknown_operation_is_a_warning_not_an_error() -> None:
    """It must not block: UIs roll out operations independently of agents."""
    finding = check_call("nosuchop", (), electron_view(), "owner")
    assert finding is not None
    assert finding.level == "WARNING" and finding.code == UNKNOWN_OPERATION
    assert "nosuchop" in finding.message and "electron" in finding.message


def test_base_only_stays_quiet_about_unknown_operations() -> None:
    """Base-only, a typo and a legitimate extension operation are indistinguishable."""
    assert check_call("nosuchop", (), base_view(), "owner") is None


def test_base_arity_is_enforced_even_base_only() -> None:
    """Base operations are fully known offline, so their arity is a hard error."""
    with pytest.raises(ValueError, match="requires arguments"):
        check_call("gt", ("a",), base_view(), "owner")
