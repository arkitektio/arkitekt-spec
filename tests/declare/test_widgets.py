"""Test the widgets for rekuest"""

import pytest
from pydantic import ValidationError

from arkitekt_spec.actions import (
    ArgPortInput,
    AssignWidgetInput,
    CustomAssignWidgetInput,
    DefinitionInput,
    definition_hash,
)
from arkitekt_spec.blok.catalogs import default_view
from arkitekt_spec.declare.definition.catalogs import widget_tree_findings
from arkitekt_spec.declare.widgets import BsxWidget, CustomWidget, SearchWidget, SliderWidget


def test_search_widget_error_on_wrong_graphql() -> None:
    """Test the search widget error when the GraphQL query is wrong"""
    with pytest.raises(ValueError):
        SearchWidget(query="hallo", ward="mikro")

    with pytest.raises(ValueError):
        SearchWidget(query="query search {}", ward="mikro")

    with pytest.raises(ValueError):
        SearchWidget(query="query search($name: sss) {}", ward="mikro")

    with pytest.raises(ValueError):
        SearchWidget(
            query="query search($search: String) {lala {value: x label: y}}",
            ward="mikro",
        )

    with pytest.raises(ValueError):
        SearchWidget(
            query="query search($search: String) {options: karl {x label: y}}",
            ward="mikro",
        )


def test_search_widget() -> None:
    """Test if it correctly generates a search widget"""
    SearchWidget(
        query=(
            "query search($search: String, $values: [ID]){ options: karl { value: x label: y}} "
        ),
        ward="mikro",
    )


def test_slider_widget_error() -> None:
    """Test the slider widget error handling"""
    with pytest.raises(ValidationError):
        SliderWidget(min=1, max=0)

    with pytest.raises(ValidationError):
        SliderWidget(min=0)


def test_slider_widget() -> None:
    """Test the slider widget"""
    SliderWidget(
        min=0,
        max=100,
    )


def test_custom_widgets_render_catalog_components() -> None:
    """Custom widgets name a catalog component and carry pure props; members enforce their fields."""
    from pydantic import ValidationError

    from arkitekt_spec.actions import (
        ActionArgumentInput,
        ChoiceInput,
        ChoiceReturnWidgetInput,
        ComponentPropInput,
        CustomAssignWidgetInput,
        UtilCallInput,
    )
    from arkitekt_spec.declare.widgets import CustomReturnWidget, CustomWidget, StringWidget

    widget = CustomWidget(
        "Gauge",
        props=[
            ComponentPropInput(
                key="max",
                util_call=UtilCallInput(
                    operation="gt",
                    arguments=(ActionArgumentInput(key="a", value_path="value"),),
                ),
            )
        ],
        dependencies=["other"],
        fallback=StringWidget(),
    )
    assert widget.kind == "CUSTOM"
    assert widget.component == "Gauge"
    assert widget.fallback is not None and widget.fallback.kind == "STRING"

    returned = CustomReturnWidget("Gauge")
    assert returned.kind == "CUSTOM" and returned.component == "Gauge"

    with pytest.raises(ValidationError, match="component"):
        CustomAssignWidgetInput()
    with pytest.raises(ValidationError, match="ward"):
        CustomAssignWidgetInput(component="X", ward="w")
    with pytest.raises(ValidationError, match="choices"):
        ChoiceReturnWidgetInput(choices=(ChoiceInput(value="a", label="A"),))


def test_choice_widgets_carry_choices_off_the_wire() -> None:
    """Choice widgets keep the choices for the port but never serialise them."""
    from arkitekt_spec.declare.widgets import ChoiceReturnWidget, ChoiceWidget, withChoices

    widget = ChoiceWidget(["a", "b"])
    assert [c.value for c in widget.choices] == ["a", "b"]
    assert widget.model_dump(by_alias=True, exclude_none=True) == {"kind": "CHOICE"}
    assert withChoices(1, 2).choices[0].value == "1"
    assert ChoiceReturnWidget(["x"]).model_dump(by_alias=True, exclude_none=True) == {"kind": "CHOICE"}


def test_state_choice_needs_exactly_one_pointer() -> None:
    """A state choice widget is either a static pointer or a computed one."""
    from arkitekt_spec.actions import (
        ActionArgumentInput,
        StateChoiceAssignWidgetInput,
        UtilCallInput,
    )

    call = UtilCallInput(
        operation="sel", arguments=(ActionArgumentInput(key="a", value_path="value"),)
    )
    StateChoiceAssignWidgetInput(state_path="/x")
    StateChoiceAssignWidgetInput(state_call=call)
    with pytest.raises(ValueError, match="exactly one of state_path or state_call"):
        StateChoiceAssignWidgetInput()
    with pytest.raises(ValueError, match="exactly one of state_path or state_call"):
        StateChoiceAssignWidgetInput(state_path="/x", state_call=call)


def test_structure_picker_is_the_catalog_picker_carrying_its_own_query() -> None:
    """The query is a prop of the component; the plain search over it is the fallback."""
    from arkitekt_spec.declare.widgets import StructurePicker

    query = (
        "query Lenses($search: String, $values: [ID!], $requires: [DescriptorConstraintInput!], $path: ID) {"
        " options: lenses(filters: {search: $search, ids: $values, requires: $requires, reachableFrom: $path}) { value: id label } }"
    )
    widget = StructurePicker(query, "mikro", ["path"], placeholder="Pick a volume")

    assert widget.kind == "CUSTOM" and widget.component == "StructurePicker"
    assert widget.dependencies == ("path",)
    props = {prop.key: prop.static_value for prop in widget.props}
    assert props["bind"] == "value" and props["ward"] == "mikro" and props["placeholder"] == "Pick a volume"
    assert "$path: ID" in props["query"] and "reachableFrom: $path" in props["query"]
    # A UI without the component still gets a working search over the same query.
    assert widget.fallback.kind == "SEARCH"
    assert widget.fallback.query == props["query"] and widget.fallback.dependencies == ("path",)


def test_structure_picker_refuses_a_variable_no_argument_backs() -> None:
    """A variable the picker does not supply must be one of its dependencies, as for a search."""
    from arkitekt_spec.declare.widgets import StringWidget, StructurePicker

    query = "query Lenses($search: String, $values: [ID!], $path: ID) { options: lenses(filters: {search: $search, ids: $values, reachableFrom: $path}) { value: id label } }"
    with pytest.raises(ValueError, match="path"):
        StructurePicker(query, "mikro")

    custom = StructurePicker(query, "mikro", ["path"], fallback=StringWidget())
    assert custom.fallback.kind == "STRING"


TREE_SERVER_HASH = "f30bf99443ae0d7f48a85b851704bf43459fa83511d5c05ed521b3be698eed1c"

TREE = """
<Html>
  <Slider bind={value} min={0} max={limit} disabled={true} />
  <p>{value}</p>
</Html>
"""


def _tree_definition(kind: str, widget: AssignWidgetInput) -> DefinitionInput:
    return DefinitionInput(
        key="k",
        name="n",
        version="0.1.0",
        kind="FUNCTION",
        args=(
            ArgPortInput(key="scale", kind=kind, widget=widget, label="s"),
            ArgPortInput(key="limit", kind="INT", label="l"),
        ),
    )


def test_a_tree_hashes_as_the_server_hashes_it() -> None:
    """Digest computed once by the rekuest server's `unique_hash` (takt's is held to it by its fixtures)."""
    definition = _tree_definition("FLOAT", BsxWidget(TREE, dependencies=["limit"]))
    assert definition_hash(definition) == TREE_SERVER_HASH


def test_a_widget_without_a_tree_hashes_as_it_did() -> None:
    assert "children" not in CustomWidget("X").model_dump()
    assert "children" not in CustomAssignWidgetInput(component="X", children=()).model_dump()


@pytest.mark.parametrize(
    "source",
    ["<Html><p>{nope}</p></Html>", "<Html><Button onClick={self.go()}>x</Button></Html>"],
)
def test_a_tree_reads_only_its_scope_and_calls_no_agent(source: str) -> None:
    with pytest.raises(ValueError):
        BsxWidget(source)


def test_value_is_typed_from_the_port() -> None:
    definition = _tree_definition("STRING", BsxWidget(TREE, dependencies=["limit"]))
    assert [finding.code for _, finding in widget_tree_findings(definition, default_view())] == ["local.bind_kind"]

