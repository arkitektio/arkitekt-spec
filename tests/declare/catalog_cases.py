"""Shared fixtures and a case table for the catalog rules.

The table is the behaviour check: one row per rule outcome, parametrized by
``test_catalog_rules``. It is written to be copy-importable by the server's own suite,
so the two sides can be held to the same rows.
"""

from arkitekt_spec.declare.catalogs import (
    UNKNOWN_OPERATION,
    ArgumentSpec,
    Catalog,
    ComponentSpec,
    OperationSpec,
    PropSpec,
    resolve_catalogs,
)

SLIDER = ComponentSpec(
    name="Slider",
    accepts_children=False,
    props=(
        PropSpec(key="min", kind="FLOAT", required=True),
        PropSpec(key="max", kind="FLOAT", required=False),
        PropSpec(key="onChange", kind="CALLBACK", required=False),
    ),
)
BOX = ComponentSpec(name="Box", accepts_children=True, props=())

FOREACH = ComponentSpec(
    name="foreach",
    accepts_children=True,
    props=(
        PropSpec(key="items", kind="LIST", required=True),
        PropSpec(key="let", kind="STRING", required=True),
    ),
)
"""BSX's own loop node. The server has no concept of it, so it must be registered
like any other component -- see ``test_foreach_is_an_ordinary_component_to_a_catalog``."""

CLAMP = OperationSpec(
    name="clamp",
    returns="FLOAT",
    arguments=(
        ArgumentSpec(key="value", kind="FLOAT", required=True),
        ArgumentSpec(key="min", kind="FLOAT", required=False),
    ),
)

ELECTRON = Catalog(name="electron", operations=(CLAMP,), components=(SLIDER, BOX))
"""A catalog that has registered both components and operations."""

NAMED_ONLY = Catalog(name="electron", operations=(), components=())
"""A catalog that exists as a name but registered nothing."""


def electron_view():
    """The view for a fully registered ``electron``."""
    return resolve_catalogs([ELECTRON], authoritative=True)


def base_view():
    """Base only: no components, operations not authoritative."""
    return resolve_catalogs([], authoritative=False)


OK = "ok"
ERROR = "error"
WARNING = f"warning:{UNKNOWN_OPERATION}"

CALL_CASES = (
    # (id, operation, argument keys, authoritative, expectation)
    ("base operation, right arguments", "gt", ("a", "b"), True, OK),
    ("base operation, unknown argument", "gt", ("a", "nope"), True, ERROR),
    ("base operation, missing required", "gt", ("a",), True, ERROR),
    ("extension operation, right arguments", "clamp", ("value",), True, OK),
    ("extension operation, optional omitted", "clamp", ("value",), True, OK),
    ("extension operation, unknown argument", "clamp", ("value", "nope"), True, ERROR),
    ("extension operation, missing required", "clamp", (), True, ERROR),
    ("unknown operation, authoritative", "nosuchop", (), True, WARNING),
    ("unknown operation, base only", "nosuchop", (), False, OK),
)

COMPONENT_CASES = (
    # (id, component, prop keys, has children, callback bindings, expectation)
    ("known component", "Slider", ("min",), False, {}, OK),
    ("unknown component", "Slidr", (), False, {}, ERROR),
    ("children on a leaf", "Slider", ("min",), True, {}, ERROR),
    ("children on a container", "Box", (), True, {}, OK),
    ("unknown prop", "Slider", ("min", "nope"), False, {}, ERROR),
    ("missing required prop", "Slider", ("max",), False, {}, ERROR),
    ("callback prop bound", "Slider", ("min", "onChange"), False, {"onChange": True}, OK),
    ("callback prop unbound", "Slider", ("min", "onChange"), False, {"onChange": False}, ERROR),
)
