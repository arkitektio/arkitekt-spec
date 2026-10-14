"""``to_bsx`` prints a tree that ``bsx`` parses back into the same tree, or refuses."""

import json

import pytest

from arkitekt_spec.actions import (
    ActionArgumentInput,
    AgentDependencyInput,
    ComponentNodeInput,
    ComponentPropInput,
    DynamicValueInput,
)
from arkitekt_spec.declare.blok import bsx, demo_state_for, to_bsx

# Every construct the parser knows, in the spellings bloks in the wild use.
SOURCES = {
    "layout": """
        <Card className="w-[380px]">
            <CardHeader>
                <CardTitle text="Opentrons Handler" />
                <CardDescription text="Manage protocol execution &amp; deck status." />
            </CardHeader>
            <CardContent className="grid gap-4">
                <Badge text="IDLE" />
                <Badge text="BUSY" />
            </CardContent>
        </Card>
    """,
    "explicit ids": """
        <Card id="root">
            <if when="@run.failed" then="error" />
            <Alert id="error" title="Failed" description="@run.error" />
        </Card>
    """,
    "paths and structures": """
        <Field label="Image">
            <StructurePicker identifier="@mikro/arraydataset" bind="form.image" />
            <StructureViewer value="@form.image" height="12rem" />
            <Text text="$state.opentrons.environment.current_temperature" />
        </Field>
    """,
    "calls": """
        <div>
            <Button label="Go"
                onClick="@self.deconvolve(image=form.image, iterations=15, scale=-1.5, on=True).into(run)" />
            <Progress value="@run.progress" />
            <Slider onChange="@self.move(name=event.name, position=event.value)" />
            <Button label="Whole event" onClick="@self.select(event)" />
            <Badge className="@utils.gt(opentrons.environment.current_temperature, 34)" />
            <Text text="@utils.math.add(a=1, b=self.counter.value)" />
            <Button onClick="@actions.self.run(items=[1, 'two', form.three], opts={'k': 'v', 'n': {'deep': True}})" />
            <Button onClick="@self.nested(value=utils.math.add(a=1, b=2), other=self.probe(x=1))" />
        </div>
    """,
    "foreach": """
        <div>
            <foreach items="@opentrons.protocol.available_protocols" let="#protocol">
                <Button label="@protocol" onClick="@opentrons.run_protocol(protocol)" />
            </foreach>
        </div>
    """,
    "quotes and escapes": """
        <Button label="Say &quot;hi&quot; &amp; 'bye'"
            checks='[{"path": "form.image", "operator": "nonEmpty"}]'
            text="a &lt; b"
            color="#fff" />
    """,
}


@pytest.mark.parametrize("name", list(SOURCES))
def test_printing_and_parsing_again_gives_the_same_tree(name: str) -> None:
    tree = bsx(SOURCES[name])

    printed = to_bsx(tree)

    assert bsx(printed) == tree
    # And the print is a fixed point: printing the reparse changes nothing.
    assert to_bsx(bsx(printed)) == printed


def test_a_stored_component_tree_reads_back_from_its_snake_case_json() -> None:
    """What the server hands out as ``componentTree`` is the snake_case dump."""
    tree = bsx(SOURCES["calls"])

    stored = json.loads(json.dumps(tree.model_dump(mode="json", exclude_none=True)))

    assert to_bsx(ComponentNodeInput.model_validate(stored)) == to_bsx(tree)


def test_structural_ids_are_left_out_and_explicit_ones_kept() -> None:
    printed = to_bsx(bsx(SOURCES["explicit ids"]))

    assert '<Card id="root">' in printed
    assert 'id="error"' in printed
    assert "if id=" not in printed


def test_values_with_double_quotes_are_single_quoted() -> None:
    printed = to_bsx(bsx(SOURCES["quotes and escapes"]))

    assert """checks='[{"path": "form.image", "operator": "nonEmpty"}]'""" in printed


def test_a_long_element_puts_one_attribute_per_line() -> None:
    printed = to_bsx(bsx(SOURCES["calls"]))

    assert "\n  <Button\n    label=\"Go\"\n" in printed


def _node(*props: ComponentPropInput) -> ComponentNodeInput:
    return ComponentNodeInput(id="Text", component="Text", props=props)


@pytest.mark.parametrize(
    ("prop", "reason"),
    [
        (ComponentPropInput(key="size", static_value=3), "BSX attributes are text"),
        (ComponentPropInput(key="text", static_value="@not.a.path"), "read as an expression"),
        (ComponentPropInput(key="text", static_value="$state.x.y"), "read as an expression"),
        (ComponentPropInput(key="text", static_value=" padded"), "stripped"),
        (
            ComponentPropInput(key="text", dynamic_value=DynamicValueInput(path="a.b", literal="x")),
            "fallback literal",
        ),
        (ComponentPropInput(key="text", dynamic_value=DynamicValueInput(path="items.0")), "not a name"),
        (ComponentPropInput(key="id", static_value="x"), "node id"),
    ],
)
def test_what_bsx_cannot_spell_is_refused(prop: ComponentPropInput, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        to_bsx(_node(prop))


def test_a_positional_argument_after_a_named_one_is_refused() -> None:
    tree = bsx('<Button onClick="@self.run(1, x=2)" />')
    call = tree.props[0].agent_call  # type: ignore[index]
    assert call is not None
    shuffled = call.model_copy(
        update={"arguments": (call.arguments[1], call.arguments[0])}  # type: ignore[index]
    )
    node = tree.model_copy(
        update={"props": (tree.props[0].model_copy(update={"agent_call": shuffled}),)}  # type: ignore[index]
    )

    with pytest.raises(ValueError, match="out of order"):
        to_bsx(node)


def test_an_argument_path_named_event_is_refused() -> None:
    """Only ``$event`` is the event; a state root literally named ``event`` would turn into it."""
    tree = bsx('<Button onClick="@self.run(x=form.x)" />')
    call = tree.props[0].agent_call  # type: ignore[index]
    assert call is not None
    renamed = call.model_copy(update={"arguments": (ActionArgumentInput(key="x", value_path="event.x"),)})
    node = tree.model_copy(
        update={"props": (tree.props[0].model_copy(update={"agent_call": renamed}),)}  # type: ignore[index]
    )

    with pytest.raises(ValueError, match="triggering event"):
        to_bsx(node)


# --- demo_state_for --------------------------------------------------------


def test_demo_state_for_adds_an_idle_record_per_bound_call_and_keeps_the_rest() -> None:
    tree = bsx('<Button onClick="@self.blur(sigma=form.sigma).into(run)" />')

    state = demo_state_for(tree, [AgentDependencyInput(key="self")], {"form": {"sigma": 2}})

    assert state["form"] == {"sigma": 2}
    assert state["run"]["status"] == "idle"
    assert state["run"]["running"] is False


def test_demo_state_for_does_not_touch_its_input() -> None:
    tree = bsx('<Button onClick="@self.blur().into(run)" />')
    given = {"form": {}}

    demo_state_for(tree, [AgentDependencyInput(key="self")], given)

    assert given == {"form": {}}
