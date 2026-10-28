"""The parity corpus: every case does what it is listed as doing, and the files describe themselves."""

import json
from pathlib import Path

from arkitekt_spec.blok.corpus import build_v2, write


def test_every_case_does_what_it_is_listed_as_doing() -> None:
    corpus = build_v2()  # raises when a case disagrees with the spec

    kinds = {("tree" in case, "error" in case, "codes" in case) for case in corpus["cases"]}  # type: ignore[union-attr, operator]
    assert kinds == {(True, False, False), (False, True, False), (True, False, True)}


def test_the_corpus_says_what_it_was_parsed_with() -> None:
    corpus = build_v2()

    assert corpus["vocab"] == 2
    assert corpus["catalogs"] == {"base": 2, "ui": 1, "scene": 1, "logo": 1, "structure": 1, "html": 1, "shadcn": 1}
    assert corpus["text_table"]["Button"]["catalog"] == "ui@1"  # type: ignore[index, call-overload]
    assert corpus["text_table"]["Button"]["text_prop"] == "label"  # type: ignore[index, call-overload]


def test_it_is_written_as_json(tmp_path: Path) -> None:
    v2 = write(tmp_path)

    assert json.loads(v2.read_text())["cases"]


def test_a_query_effect_named_by_a_task_call_is_refused() -> None:
    """``query.refetch`` names its query by a literal: a task call in its place (which the BSX
    cannot write, but a stored tree can) is ``query.effect_target``, as takt and rekuest_core say."""
    from arkitekt_spec.actions import AgentProbeInput
    from arkitekt_spec.blok.bsx import parse_bsx
    from arkitekt_spec.blok.catalogs import default_view
    from arkitekt_spec.declare.blok.validate import BlokScope, check_blok_tree

    tree = parse_bsx('<Button onClick={query.refetch("positions")} />')
    (prop,) = [p for p in tree.props or () if p.key == "onClick"]
    assert prop.util_call is not None
    (named,) = prop.util_call.arguments or ()
    probe = named.model_copy(update={"value_literal": None, "agent_call": AgentProbeInput(dependency="self", operation="run")})
    call = prop.util_call.model_copy(update={"arguments": (probe,)})
    tree = tree.model_copy(update={"props": (prop.model_copy(update={"util_call": call}),)})

    findings = check_blok_tree(tree, BlokScope(kind="agent", context="HTML"), default_view())

    assert [(f.code, f.message) for f in findings if f.code == "query.effect_target"] == [
        ("query.effect_target", "query.refetch names its query by a literal name (queries.<name>.refetch()), not a task call.")
    ]
