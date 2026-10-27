"""The prompt a model writes bloks from: generated from the vocabulary and the catalogs."""

from arkitekt_spec.blok.catalogs import default_view
from arkitekt_spec.blok.prompt import (
    blok_prompt,
    component_signature,
    drawn_in,
    effect_signature,
    function_signature,
    roots_text,
)
from arkitekt_spec.blok.vocabulary import load_vocabulary


def test_a_signature_names_the_text_prop_the_values_and_the_event() -> None:
    view = default_view()
    button, select = view.component("Button"), view.component("Select")
    assert button is not None and select is not None

    line = component_signature(button)

    assert line.startswith("Button[label](") and '"outline"' in line and "className" not in line
    assert line.endswith("— A clickable button.")
    assert "onChange: CALLBACK(event.value)" in component_signature(select)


def test_a_placement_rule_is_spelled_out() -> None:
    case = default_view().component("case")
    assert case is not None

    assert "inside match" in component_signature(case)


def test_the_prompt_lists_every_component_drawn_in_its_context_and_every_function() -> None:
    view = default_view()

    prompt = blok_prompt(view, kind="custom", context="HTML")

    listed = {line[2:].split("(")[0].split("[")[0] for line in prompt.splitlines() if line.startswith("- ")}
    for component in drawn_in(view, "HTML"):
        assert component.name in listed, component.name
    assert view.component("Model") is not None and "Model" not in listed
    for function in view.functions.values():
        if not function.effect:
            assert function_signature(function) in prompt
        elif function.name != "task.answer":
            assert effect_signature(function) in prompt
    assert "task.answer" not in prompt and "queries.<name>.refetch()" in prompt


def test_an_effect_on_the_task_is_listed_only_where_the_blok_reads_one() -> None:
    assert "task.answer(" in blok_prompt(kind="task", components=())


def test_the_roots_are_the_kinds() -> None:
    custom, agent = roots_text("custom"), roots_text("agent")

    assert "- deps:" in custom and "- self:" not in custom
    assert "- self:" in agent and "- deps:" not in agent


def test_the_records_come_from_the_vocabulary() -> None:
    task = load_vocabulary().records["task"]
    status = task.field("status")
    assert status is not None

    assert "status: " + "|".join(status.choices) in blok_prompt(components=())


def test_a_short_prompt_lists_only_the_components_asked_for() -> None:
    prompt = blok_prompt(components=["Text"])

    assert "- Text[text](" in prompt and "- Button[" not in prompt
