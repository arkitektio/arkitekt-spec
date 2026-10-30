"""Runtime formulas and Fits: parsed into the blok vocabulary, and limited to what they may read."""

import json
from pathlib import Path

import pytest

from arkitekt_spec.actions import (
    ActionKind,
    ArgPortInput,
    DefinitionInput,
    EstimatedRuntimeInput,
    FitsInput,
    ImplementationInput,
    PortKind,
    definition_hash,
)
from arkitekt_spec.declare.blok.parser import parse_estimated_runtime, parse_fits
from arkitekt_spec.declare.descriptors import fits_holds

CORPUS = json.loads((Path(__file__).parent.parent / "fixtures" / "fits_corpus.json").read_text())


def _definition() -> DefinitionInput:
    return DefinitionInput(
        name="Segment",
        key="segment",
        version="1",
        kind=ActionKind.FUNCTION,
        args=(
            ArgPortInput(key="image", kind=PortKind.STRUCTURE, identifier="@mikro/arraylens"),
            ArgPortInput(key="other", kind=PortKind.STRUCTURE, identifier="@mikro/arraylens"),
            ArgPortInput(key="scales", kind=PortKind.INT),
        ),
    )


def _estimate(formula: str, ui: str | None = None) -> ImplementationInput:
    return ImplementationInput(
        interface="segment",
        definition=_definition(),
        estimated_runtime=EstimatedRuntimeInput(call=parse_estimated_runtime(formula), source=formula, ui_call=parse_estimated_runtime(ui) if ui else None),
    )


def test_a_formula_names_what_the_renderer_implements() -> None:
    call = parse_estimated_runtime('args.image["@mikro/size"] / agent.cpu_count')

    assert call.operation == "math.div"
    assert [argument.value_path for argument in call.arguments] == ["args/image/@mikro~1size", "agent/cpu_count"]


@pytest.mark.parametrize("formula", ["args.scales * 2", "args.image[\"@mikro/size\"] ** 2", "math.sqrt(args.image[\"@mikro/size\"]) * 2", "agent.state.camera.exposure * 2", "args.image[\"@mikro/size\"] + args.other[\"@mikro/size\"]"])
def test_the_estimate_is_what_the_server_can_compute(formula: str) -> None:
    with pytest.raises(ValueError):
        _estimate(formula)


def test_the_form_has_the_rest_but_not_agent_state() -> None:
    _estimate('args.image["@mikro/size"] * 2', ui='math.sqrt(args.image["@mikro/size"]) * args.scales ** 2')

    with pytest.raises(ValueError, match="an agent has"):
        _estimate('args.image["@mikro/size"] * 2', ui="agent.state.camera.exposure * 2")


@pytest.mark.parametrize(
    "formula",
    [
        "__import__('os').system('x')",
        "@mikro/size ** 2 < 4",
        "@mikro/size != 4",
        "not @mikro/size < 4",
        "agent.state.camera.exposure < 4",
        "args.scales < 4",
        "@mikro/size * 2",
    ],
)
def test_a_fits_is_a_predicate_and_never_runs_anything(formula: str) -> None:
    with pytest.raises(ValueError):
        parse_fits(formula)


def test_a_fits_goes_on_a_single_structure_argument() -> None:
    call = parse_fits("@mikro/size < 4")

    with pytest.raises(ValueError, match="only a single structure"):
        ImplementationInput(interface="segment", definition=_definition(), fits=(FitsInput(port="scales", call=call),))


def test_neither_changes_the_action_hash() -> None:
    assert definition_hash(_estimate('args.image["@mikro/size"] * 2').definition) == definition_hash(_definition())


@pytest.mark.parametrize("row", CORPUS["fits"], ids=lambda row: row["formula"])
def test_the_corpus(row: dict) -> None:
    call = parse_fits(row["formula"])

    assert call.model_dump(mode="json", exclude_none=True) == row["call"]
    assert fits_holds(call, row["object"], row["hardware"]) is row["expected"]
