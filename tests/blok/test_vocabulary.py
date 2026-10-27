"""The vocabulary: the one table every party of the blok language reads."""

import json

import pytest

from arkitekt_spec.blok.vocabulary import (
    BlokError,
    Finding,
    load_vocabulary,
    parse_vocabulary,
    vocabulary_text,
)


def test_it_speaks_v2_and_reserves_one_list() -> None:
    vocabulary = load_vocabulary()

    assert vocabulary.vocab == 2
    assert {"self", "deps", "queries", "subscriptions", "args", "task", "event", "viewer", "then"} <= vocabulary.reserved
    assert {"cmp", "logic", "math", "str", "list", "obj", "val", "fmt", "time", "ui", "query", "mutations"} <= vocabulary.reserved
    assert "now" not in vocabulary.reserved


def test_then_replaces_now_in_every_kind_but_the_action_logo() -> None:
    kinds = load_vocabulary().kinds

    for name in ("agent", "custom", "task", "action"):
        assert "then" in kinds[name].roots and "now" not in kinds[name].roots
    assert "then" not in kinds["action_logo"].roots


def test_catalogs_are_versioned_and_kinds_compatible() -> None:
    vocabulary = load_vocabulary()

    assert vocabulary.catalogs == {"ui": 1, "scene": 1, "logo": 1, "structure": 1, "html": 1}
    assert vocabulary.base_version == 2
    assert vocabulary.bind_kinds["FLOAT"] >= {"FLOAT", "INT"} and vocabulary.bind_kinds["BOOL"] == {"BOOL"}
    assert "ANY" not in vocabulary.bind_kinds, "an ANY control writes any local"
    assert "FLOAT" in vocabulary.arg_kinds["INT"] and "INT" not in vocabulary.arg_kinds["FLOAT"]
    assert set(vocabulary.theme.data_attributes) == {"busy", "stale"}


def test_the_query_record_fetches_more() -> None:
    record = load_vocabulary().records["query"]

    assert [field.key for field in record.fields] == ["status", "value", "error", "refetching", "fetching_more"]


def test_the_root_matrix_is_the_designs() -> None:
    kinds = load_vocabulary().kinds

    assert "deps" not in kinds["agent"].roots and "self" in kinds["agent"].roots
    assert "self" not in kinds["custom"].roots and "deps" in kinds["custom"].roots
    assert {"self", "deps", "args", "task"} <= kinds["task"].roots
    assert kinds["action"].contexts == ("HTML", "SCENE", "OVERLAY")
    assert kinds["action_logo"].roots == frozenset({"task"}) and not kinds["action_logo"].locals


def test_one_task_record_shape() -> None:
    record = load_vocabulary().records["task"]

    assert [field.key for field in record.fields] == [
        "id", "status", "running", "done", "failed", "progress", "message", "error", "args", "ask", "answer", "returns",
    ]
    assert record.field("status").choices == ("idle", "queued", "running", "asking", "done", "failed", "cancelled")  # type: ignore[union-attr]
    assert load_vocabulary().idle_task["status"] == "idle"


def test_functions_live_in_namespaces_with_effects_flagged() -> None:
    functions = load_vocabulary().functions

    assert all("." in name for name in functions)
    assert functions["ui.copy"].effect and functions["task.answer"].effect
    assert functions["query.refetch"].effect and functions["query.fetch_more"].effect
    assert functions["query.fetch_more"].keys == ("query", "variables") and functions["query.fetch_more"].required_keys == ("query",)
    assert not functions["str.upper"].effect
    assert functions["logic.choose"].lazy and functions["logic.all"].lazy
    assert not {"fmt.relative_time", "time.since", "time.until"} & set(functions), "nothing reads the wall clock"
    assert functions["time.diff"].keys == ("start", "end")
    assert functions["val.status"].sees_status
    assert all(function.null for function in functions.values()), "every function says what it does with null"
    assert functions["str.truncate"].keys == ("value", "length", "suffix")
    assert functions["str.truncate"].required_keys == ("value", "length")


def test_operators_desugar_to_functions_that_exist() -> None:
    vocabulary = load_vocabulary()

    for operator, function in vocabulary.operators.items():
        if operator != "precedence":
            assert function in vocabulary.functions, operator


def test_the_theme_has_status_roles_and_logo_pens() -> None:
    theme = load_vocabulary().theme

    for role in ("success", "warning", "info"):
        assert {role, f"{role}-foreground", f"{role}-soft"} <= set(theme.colour_roles)
        assert role in theme.logo_pens
    assert {"ink", "brand", "none"} <= set(theme.logo_pens)
    assert "data-busy" in theme.custom_variants


def test_ids_explicit_and_derived_are_told_apart() -> None:
    vocabulary = load_vocabulary()

    assert vocabulary.is_explicit_id("arm-pose")
    assert not vocabulary.is_explicit_id("Card[0]/Button[1]")
    assert not vocabulary.is_explicit_id("Card[0]/#0")


def test_a_finding_is_a_row_of_the_table_filled() -> None:
    finding = Finding.of("roots.unknown", "Card[0]", path="x.y", root="x")

    assert (finding.code, finding.level, finding.path) == ("roots.unknown", "error", "Card[0]")
    assert "x.y reads 'x'" in finding.message
    assert finding.to_json()["code"] == "roots.unknown"


def test_an_error_carries_its_code_and_place() -> None:
    error = BlokError("bsx.empty", line=3, column=1)

    assert (error.code, error.line, error.column) == ("bsx.empty", 3, 1)
    assert str(error).startswith("line 3, column 1:") and "[bsx.empty]" in str(error)


def test_a_vocabulary_that_contradicts_itself_is_refused() -> None:
    raw = json.loads(vocabulary_text())
    raw["kinds"]["agent"]["roots"].append("nowhere")
    with pytest.raises(ValueError, match="unknown roots"):
        parse_vocabulary(raw)

    raw = json.loads(vocabulary_text())
    raw["reserved"].remove("mutations")
    with pytest.raises(ValueError, match="reserved differs"):
        parse_vocabulary(raw)


def test_no_action_port_is_any_so_no_local_passes_to_one() -> None:
    assert all("ANY" not in kinds for kinds in load_vocabulary().arg_kinds.values())


def test_coalesce_is_an_operator() -> None:
    assert load_vocabulary().operators["??"] == "logic.coalesce"


def test_the_spectral_roles_are_colours_and_pens() -> None:
    theme = load_vocabulary().theme

    assert theme.spectral_roles == tuple(f"spectrum-{band}" for band in ("violet", "blue", "cyan", "green", "yellow", "orange", "red"))
    for role in theme.spectral_roles:
        assert role in theme.colour_roles and f"{role}-soft" in theme.colour_roles and role in theme.logo_pens


def test_a_runtime_code_is_no_checkers() -> None:
    diagnostics = load_vocabulary().diagnostics

    assert diagnostics["deps.unbound"].runtime
    assert not any(spec.runtime for code, spec in diagnostics.items() if code != "deps.unbound")
    assert "catalog.conflict" not in diagnostics and diagnostics["catalog.shadow"].level == "warning"


def test_a_hint_is_filled_like_its_message() -> None:
    finding = Finding.of("catalog.value", "Html[0]/Button[0]", component="Button", key="variant", value="loud", values="default, outline")

    assert finding.hint == "Write one of default, outline."
    assert finding.fields["value"] == "loud"
    assert finding == Finding.of("catalog.value", "Html[0]/Button[0]", component="Button", key="variant", value="loud", values="default, outline").placed(3, 5)


def test_a_finding_of_a_parsed_source_says_where_it_is() -> None:
    from arkitekt_spec.declare.blok.validate import validate_bsx

    (finding,) = validate_bsx('<Html>\n  <Card>\n    <Button variant="loud">Go</Button>\n  </Card>\n</Html>')

    assert (finding.code, finding.line, finding.column) == ("catalog.value", 3, 5)
    with pytest.raises(BlokError) as refused:
        validate_bsx("<Html><Card>")
    assert (refused.value.code, refused.value.line) == ("bsx.unclosed", 1)
    assert refused.value.hint == "Close it with </Card>, or write it self-closing as <Card ... />."


def test_every_hint_fills_from_its_message_fields() -> None:
    import string

    for code, spec in load_vocabulary().diagnostics.items():
        if spec.hint is None:
            continue
        message_fields = {name for _, name, _, _ in string.Formatter().parse(spec.message) if name}
        hint_fields = {name for _, name, _, _ in string.Formatter().parse(spec.hint) if name}
        assert hint_fields <= message_fields, code
