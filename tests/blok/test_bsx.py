"""The v2 parser: JSX-like markup, the JS-subset expressions, and the stored tree they become."""

import pytest

from arkitekt_spec.actions import ComponentNodeInput, ComponentPropInput
from arkitekt_spec.blok.bsx import jsx_text, parse_argument, parse_bsx
from arkitekt_spec.blok.vocabulary import BlokError


def prop(node: ComponentNodeInput, key: str) -> ComponentPropInput:
    return next(p for p in node.props or () if p.key == key)


def child(node: ComponentNodeInput, index: int = 0) -> ComponentNodeInput:
    return (node.children or ())[index]


def refused(source: str) -> BlokError:
    with pytest.raises(BlokError) as caught:
        parse_bsx(source)
    return caught.value


# -- markup


def test_ids_are_derived_by_name_and_occurrence() -> None:
    tree = parse_bsx("<Card><Button /><Text /><Button /></Card>")

    assert tree.id == "Card[0]"
    assert [c.id for c in tree.children or ()] == ["Card[0]/Button[0]", "Card[0]/Text[0]", "Card[0]/Button[1]"]


def test_inserting_an_element_renames_no_sibling_of_another_name() -> None:
    before = parse_bsx("<Card><Button /><Text /></Card>")
    after = parse_bsx("<Card><Badge /><Button /><Text /></Card>")

    assert child(before, 0).id == child(after, 1).id
    assert child(before, 1).id == child(after, 2).id


def test_an_explicit_id_roots_its_children() -> None:
    tree = parse_bsx('<Card id="panel"><Button /></Card>')

    assert (tree.id, child(tree).id) == ("panel", "panel/Button[0]")


def test_a_string_is_a_literal_a_brace_an_expression_a_bare_attribute_true() -> None:
    tree = parse_bsx('<Button label="@self.x" disabled={task.running} fullWidth />')

    assert prop(tree, "label").static_value == "@self.x"
    assert prop(tree, "disabled").dynamic_value.path == "task.running"  # type: ignore[union-attr]
    assert prop(tree, "fullWidth").static_value is True


def test_a_parse_error_says_where() -> None:
    error = refused("<Card>\n  <Button>\n</Card>")

    assert (error.code, error.line) == ("bsx.mismatched_tag", 3)


def test_an_expression_error_says_where_in_the_source() -> None:
    error = refused("<Card>\n  <Text text={a + 1} />\n</Card>")

    assert (error.code, error.line, error.column) == ("expr.arithmetic", 2, 17)


# -- text


def test_text_fills_the_components_text_prop() -> None:
    assert prop(parse_bsx("<Button>Grip</Button>"), "label").static_value == "Grip"
    assert prop(parse_bsx("<Badge>{task.status}</Badge>"), "text").dynamic_value.path == "task.status"  # type: ignore[union-attr]


def test_text_with_expressions_is_a_template() -> None:
    call = prop(parse_bsx('<Text>Arrived at {move.args.position}, {"{"}ok{"}"}</Text>'), "text").util_call
    assert call is not None and call.operation == "str.template"
    template, values = call.arguments or ()
    assert template.value_literal == "Arrived at {0}, {{ok}}"
    assert [v.value_path for v in values.value_list or ()] == ["move.args.position"]


def test_whitespace_follows_jsx() -> None:
    assert jsx_text("\n   Arrived   at\n   the   end  \n  ") == "Arrived at the end"
    assert jsx_text("a ") == "a "
    assert jsx_text("  \n  ") == ""
    assert prop(parse_bsx('<Text>{a}{" "}{b}</Text>'), "text").util_call is not None


def test_mixed_content_becomes_implicit_text_nodes() -> None:
    tree = parse_bsx("<p>Hello <span>world</span> and {viewer.user.username}</p>")

    assert [(c.id, c.component) for c in tree.children or ()] == [("p[0]/#0", "span"), ("p[0]/span[0]", "span"), ("p[0]/#1", "span")]
    assert prop(child(tree, 0), "text").static_value == "Hello "


@pytest.mark.parametrize(
    ("source", "code"),
    [
        ("<Card>hi</Card>", "bsx.text_not_accepted"),
        ("<div>hi</div>", "bsx.text_not_accepted"),
        ("<Scene><Model>x</Model></Scene>", "bsx.text_in_context"),
        ("<AgentLogo><G>x</G></AgentLogo>", "bsx.text_in_context"),
        ("<Foo>x</Foo>", "bsx.text_unknown_component"),
        ('<Button label="a">b</Button>', "bsx.text_and_prop"),
        ("<Text>{self.go()}</Text>", "bsx.text_callback"),
    ],
)
def test_text_where_it_cannot_go_is_refused(source: str, code: str) -> None:
    assert refused(source).code == code


def test_text_in_a_popout_is_html_again() -> None:
    tree = parse_bsx('<Scene><Model src={self.assets.m}><Popout node="arm"><Text>hi</Text></Popout></Model></Scene>')

    assert prop(child(child(child(tree))), "text").static_value == "hi"


# -- expressions


def test_operators_desugar_to_functions() -> None:
    call = prop(parse_bsx('<Badge when={a >= 1 && !b || c == "x"} />'), "when").util_call
    assert call is not None and call.operation == "logic.any"
    (values,) = call.arguments or ()
    first, second = values.value_list or ()
    assert first.util_call is not None and first.util_call.operation == "logic.all"
    assert second.util_call is not None and second.util_call.operation == "cmp.eq"


def test_a_ternary_is_logic_choose() -> None:
    call = prop(parse_bsx('<Button variant={a ? "default" : "outline"} />'), "variant").util_call
    assert call is not None and call.operation == "logic.choose"
    assert [a.key for a in call.arguments or ()] == ["cond", "then", "otherwise"]


def test_positional_arguments_are_named_after_the_function() -> None:
    call = prop(parse_bsx("<Text text={str.truncate(task.message, 20)} />"), "text").util_call
    assert call is not None
    assert [(a.key, a.value_path or a.value_literal) for a in call.arguments or ()] == [("value", "task.message"), ("length", 20)]


def test_a_null_argument_has_no_value() -> None:
    call = prop(parse_bsx("<Button onClick={self.go(to: null)} />"), "onClick").agent_call
    assert call is not None and (call.arguments or ())[0].model_dump(exclude_none=True) == {"key": "to"}


def test_a_literal_list_is_static_a_list_holding_expressions_is_refused() -> None:
    assert prop(parse_bsx('<Route through={["robot", "ot2"]} />'), "through").static_value == ["robot", "ot2"]
    assert refused("<Chart data={[a, 1]} />").code == "expr.dynamic_container"


def test_class_names_desugar_to_str_classes() -> None:
    call = prop(parse_bsx('<Badge className={["px-2", task.running && "animate-pulse"]} />'), "className").util_call
    assert call is not None and call.operation == "str.classes"
    call = prop(parse_bsx('<Badge className={{"animate-pulse": task.running}} />'), "className").util_call
    assert call is not None and (call.arguments or ())[0].value_dict is not None


def test_a_variable_is_parsed_as_an_argument() -> None:
    assert parse_argument("slide", "args.slide.id").value_path == "args.slide.id"
    with pytest.raises(BlokError, match="callback.nested"):
        parse_argument("x", "self.go(a: 1)")


# -- callbacks


def test_a_callback_is_one_task_call_named_by_into() -> None:
    onclick = prop(parse_bsx("<Button onClick={deps.robot.move_to(position: p.id) into move} />"), "onClick")

    assert onclick.agent_call is not None
    assert (onclick.agent_call.dependency, onclick.agent_call.operation, onclick.declares_value) == ("robot", "move_to", "move")


def test_a_refetch_and_the_effects_are_callbacks_too() -> None:
    tree = parse_bsx("<Card><Button onClick={queries.positions.refetch()} /><Button onClick={ui.copy(task.id)} /></Card>")

    refetch = prop(child(tree, 0), "onClick").util_call
    assert refetch is not None and refetch.operation == "query.refetch"
    assert [(a.key, a.value_literal) for a in refetch.arguments or ()] == [("query", "positions")]
    assert prop(child(tree, 1), "onClick").util_call.operation == "ui.copy"  # type: ignore[union-attr]


def test_fetch_more_desugars_to_its_effect_with_the_variables_as_an_object() -> None:
    call = prop(parse_bsx("<Button onClick={queries.cells.fetch_more(after: queries.cells.value.cursor)} />"), "onClick").util_call

    assert call is not None and call.operation == "query.fetch_more"
    named, variables = call.arguments or ()
    assert (named.key, named.value_literal) == ("query", "cells")
    assert variables.key == "variables" and [(v.key, v.value_path) for v in variables.value_dict or ()] == [("after", "queries.cells.value.cursor")]
    bare = prop(parse_bsx("<Button onClick={queries.cells.fetch_more()} />"), "onClick").util_call
    assert bare is not None and len(bare.arguments or ()) == 1, "no variables: the argument is left out"


@pytest.mark.parametrize(
    ("callback", "code"),
    [
        ("self.a(); self.b()", "callback.sequence"),
        ('camera = "iso"', "callback.assignment"),
        ("await self.a()", "callback.await"),
        ("self.go(1)", "callback.positional"),
        ("str.upper(x) into run", "callback.into"),
        ("str.upper(self.go(x: 1))", "callback.nested"),
    ],
)
def test_a_callback_does_exactly_one_thing(callback: str, code: str) -> None:
    assert refused(f"<Button onClick={{{callback}}} />").code == code


# -- control flow


def test_if_else_are_children() -> None:
    tree = parse_bsx("<if when={a}><Text /><else when={b}><Badge /></else><else><Spinner /></else></if>")

    assert [c.component for c in tree.children or ()] == ["Text", "else", "else"]
    assert prop(child(tree, 1), "when").dynamic_value.path == "b"  # type: ignore[union-attr]


def test_a_foreach_needs_items_and_let_its_key_is_optional() -> None:
    tree = parse_bsx('<foreach items={xs} let="p" key={p.id}><Text text={p.name} /></foreach>')

    assert prop(tree, "let").declares_value == "p"
    assert prop(tree, "key").dynamic_value.path == "p.id"  # type: ignore[union-attr]
    positional = parse_bsx('<foreach items={xs} let="p"><Text /></foreach>')
    assert [p.key for p in positional.props or ()] == ["items", "let"]
    assert refused('<foreach let="p"><Text /></foreach>').code == "bsx.foreach"
