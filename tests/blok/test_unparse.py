"""Printing a stored tree as v2 BSX: what is printed parses back into the same tree."""

import pytest

from arkitekt_spec.actions import (
    ComponentNodeInput,
    ComponentPropInput,
    DynamicValueInput,
)
from arkitekt_spec.blok.bsx import parse_bsx
from arkitekt_spec.blok.corpus import VALID
from arkitekt_spec.blok.unparse import to_bsx


@pytest.mark.parametrize(("name", "source"), VALID, ids=[name for name, _ in VALID])
def test_every_corpus_source_prints_back_into_its_tree(name: str, source: str) -> None:
    tree = parse_bsx(source)

    assert parse_bsx(to_bsx(tree)) == tree


def test_operators_print_as_operators_and_text_as_text() -> None:
    printed = to_bsx(
        parse_bsx(
            "<Card><Badge when={a && !b}>{task.status}</Badge><Text>Arrived at {x}</Text></Card>"
        )
    )

    assert "<Badge when={a && !b}>{task.status}</Badge>" in printed
    assert "<Text>Arrived at {x}</Text>" in printed


def test_derived_ids_are_left_out_and_explicit_ones_kept() -> None:
    printed = to_bsx(parse_bsx('<Card><Button id="go" /><Button /></Card>'))

    assert printed == '<Card>\n  <Button id="go" />\n  <Button />\n</Card>'


def test_text_prints_between_the_tags_on_their_line_when_it_is_the_one_child() -> None:
    assert to_bsx(parse_bsx("<Button>Go</Button>")) == "<Button>Go</Button>"
    assert (
        to_bsx(parse_bsx("<Card>Hello <Badge>new</Badge></Card>"))
        == '<Card>\n  {"Hello "}\n  <Badge>new</Badge>\n</Card>'
    )
    assert (
        to_bsx(parse_bsx("<Foo>of a catalog not known here</Foo>"))
        == "<Foo>of a catalog not known here</Foo>"
    )


def test_a_tree_stored_before_text_was_a_node_still_prints() -> None:
    """Its text prop stays the attribute it is; its implicit Text prints as the text it was."""
    label = ComponentNodeInput(
        id="Button[0]",
        component="Button",
        props=(ComponentPropInput(key="label", static_value="Go"),),
    )
    implicit = ComponentNodeInput(
        id="Card[0]",
        component="Card",
        children=(
            ComponentNodeInput(
                id="Card[0]/#0",
                component="Text",
                props=(ComponentPropInput(key="text", static_value="Hello"),),
            ),
            ComponentNodeInput(id="Card[0]/Badge[0]", component="Badge"),
        ),
    )

    assert (
        to_bsx(label) == '<Button label="Go" />' and parse_bsx(to_bsx(label)) == label
    )
    assert to_bsx(implicit) == "<Card>\n  Hello\n  <Badge />\n</Card>"
    assert [node.component for node in parse_bsx(to_bsx(implicit)).children or ()] == [
        "#text",
        "Badge",
    ]


def test_a_text_prop_stays_an_attribute() -> None:
    tree = ComponentNodeInput(
        id="Badge[0]",
        component="Badge",
        props=(
            ComponentPropInput(key="text", static_value="hi"),
            ComponentPropInput(key="variant", static_value="outline"),
        ),
    )

    assert to_bsx(tree) == '<Badge text="hi" variant="outline" />'
    assert parse_bsx(to_bsx(tree)) == tree


@pytest.mark.parametrize(
    ("tree", "reason"),
    [
        (
            ComponentNodeInput(id="Card/Button[0]", component="Card"),
            "neither explicit nor",
        ),
        (
            ComponentNodeInput(
                id="Text[0]",
                component="Text",
                props=(
                    ComponentPropInput(
                        key="text",
                        dynamic_value=DynamicValueInput(path="a", literal="b"),
                    ),
                ),
            ),
            "fallback literal",
        ),
    ],
)
def test_what_bsx_cannot_spell_is_refused(
    tree: ComponentNodeInput, reason: str
) -> None:
    with pytest.raises(ValueError, match=reason):
        to_bsx(tree)
