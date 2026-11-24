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
    assert {
        "self",
        "deps",
        "queries",
        "subscriptions",
        "args",
        "task",
        "event",
        "viewer",
        "then",
    } <= vocabulary.reserved
    assert {
        "cmp",
        "logic",
        "math",
        "str",
        "list",
        "obj",
        "val",
        "fmt",
        "time",
        "ui",
        "query",
        "mutations",
    } <= vocabulary.reserved
    assert "now" not in vocabulary.reserved


def test_then_replaces_now_in_every_kind_but_the_action_logo() -> None:
    kinds = load_vocabulary().kinds

    for name in ("agent", "custom", "task", "action"):
        assert "then" in kinds[name].roots and "now" not in kinds[name].roots
    assert "then" not in kinds["action"].drawn_in("LOGO").roots


def test_catalogs_are_versioned_and_kinds_compatible() -> None:
    vocabulary = load_vocabulary()

    assert (vocabulary.vocab, vocabulary.minor) == (2, 9)
    assert vocabulary.base_catalog == "https://arkitekt.live/catalogs/base/v4"
    assert list(vocabulary.bundled_catalogs.values()) == [
        "catalogs/base@4.json",
        "catalogs/orkestrator@1.json",
    ]
    assert vocabulary.host_parts == (
        "args",
        "arg",
        "deps",
        "dep",
        "ask",
        "returns",
        "status",
        "timeline",
        "label",
        "description",
        "message",
        "control",
        "field",
    )
    assert vocabulary.bind_kinds["FLOAT"] >= {"FLOAT", "INT"} and vocabulary.bind_kinds[
        "BOOL"
    ] == {"BOOL"}
    assert "ANY" not in vocabulary.bind_kinds, "an ANY control writes any local"
    assert (
        "FLOAT" in vocabulary.arg_kinds["INT"]
        and "INT" not in vocabulary.arg_kinds["FLOAT"]
    )
    assert set(vocabulary.theme.data_attributes) == {"busy", "stale"}


def test_the_query_record_fetches_more() -> None:
    record = load_vocabulary().records["query"]

    assert [field.key for field in record.fields] == [
        "status",
        "value",
        "error",
        "refetching",
        "fetching_more",
    ]


def test_the_root_matrix_is_the_designs() -> None:
    kinds = load_vocabulary().kinds

    # An agent blok's deps are the ones it declares itself (`<dependencies>`).
    assert {"self", "deps"} <= kinds["agent"].roots and "task" not in kinds[
        "agent"
    ].roots
    # A custom blok writes its bound agents `agents.<key>`; they are stored `deps.<key>`.
    assert not {"self", "deps"} & kinds["custom"].roots and kinds["custom"].reads(
        "deps"
    )
    assert {"self", "deps", "args", "task"} <= kinds["task"].roots
    assert kinds["action"].contexts == ("PANEL", "SCENE", "LOGO")
    # Before a run there is no task to read, and no past to travel to.
    assert {"self", "deps", "args"} <= kinds["assign"].roots and not {
        "task",
        "then",
    } & kinds["assign"].roots
    logo = kinds["action"].drawn_in("LOGO")
    assert logo.roots == frozenset({"task"}) and not logo.locals


def test_one_task_record_shape() -> None:
    record = load_vocabulary().records["task"]

    assert [field.key for field in record.fields] == [
        "id",
        "status",
        "running",
        "paused",
        "done",
        "failed",
        "progress",
        "message",
        "error",
        "args",
        "ask",
        "answer",
        "returns",
        "started_at",
        "ended_at",
        "duration",
        "log",
        "can",
    ]
    assert record.field("status").choices == (  # type: ignore[union-attr]
        "idle",
        "queued",
        "running",
        "asking",
        "paused",
        "done",
        "failed",
        "cancelled",
    )
    assert [field.key for field in record.field("can").children] == [  # type: ignore[union-attr]
        "cancel",
        "pause",
        "resume",
        "step",
        "interrupt",
    ]
    idle = load_vocabulary().idle_task
    assert idle["status"] == "idle" and set(idle) == {
        field.key for field in record.fields
    }
    assert idle["can"] == dict.fromkeys(
        ("cancel", "pause", "resume", "step", "interrupt"), False
    )


def test_assign_is_a_root_of_the_assign_kind_alone() -> None:
    vocabulary = load_vocabulary()

    assert "assign" in vocabulary.data_roots and "assign" in vocabulary.reserved
    assert [
        name for name, kind in vocabulary.kinds.items() if "assign" in kind.roots
    ] == ["assign"]
    assert [field.key for field in vocabulary.records["assign"].fields] == [
        "given",
        "missing",
        "ready",
        "starting",
        "blocked",
    ]


def test_a_roots_operations_are_effects_named_after_it() -> None:
    functions = load_vocabulary().functions

    operations = [name for name in functions if name.startswith("task.")]
    assert operations == [
        "task.answer",
        "task.cancel",
        "task.pause",
        "task.resume",
        "task.step",
        "task.interrupt",
    ]
    assert all(
        functions[name].effect and functions[name].task_operation for name in operations
    )
    assert (
        functions["assign.start"].effect
        and not functions["assign.start"].task_operation
    )


def test_an_effect_is_a_command_or_only_the_viewers() -> None:
    functions = load_vocabulary().functions.values()

    assert not [
        function.name
        for function in functions
        if function.command and not function.effect
    ]
    assert sorted(function.name for function in functions if function.command) == [
        "assign.start",
        "task.answer",
        "task.cancel",
        "task.interrupt",
        "task.pause",
        "task.resume",
        "task.step",
    ]
    assert sorted(
        function.name
        for function in functions
        if function.effect and not function.command
    ) == [
        "deps.pick",
        "port.clear",
        "port.reset",
        "port.set",
        "port.touch",
        "query.fetch_more",
        "query.refetch",
        "ui.copy",
    ]


def test_a_port_blok_is_a_field_of_a_form() -> None:
    """It reads its port, the form and the agents, writes through the port's own effects, and calls no agent."""
    vocabulary = load_vocabulary()
    port = vocabulary.kinds["port"]

    assert port.roots == {
        "self",
        "deps",
        "args",
        "port",
        "queries",
        "subscriptions",
        "viewer",
    }
    assert (port.contexts, port.locals, port.calls) == (("PANEL",), True, False)
    assert [name for name, kind in vocabulary.kinds.items() if not kind.calls] == [
        "port"
    ]
    assert [
        name for name, kind in vocabulary.kinds.items() if "port" in kind.roots
    ] == ["port"]
    assert [field.key for field in vocabulary.records["port"].fields] == [
        "value",
        "key",
        "label",
        "description",
        "kind",
        "identifier",
        "nullable",
        "default",
        "choices",
        "unit",
        "editable",
        "error",
        "invalid",
        "touched",
        "dirty",
    ]
    effects = {
        name: function
        for name, function in vocabulary.functions.items()
        if name.startswith("port.")
    }
    assert list(effects) == ["port.set", "port.clear", "port.reset", "port.touch"]
    assert all(
        function.effect and not function.command and function.needs == "port"
        for function in effects.values()
    )
    assert [
        name
        for name, function in vocabulary.functions.items()
        if function.needs is not None
    ] == list(effects)


def test_what_is_not_portable_is_listed() -> None:
    vocabulary = load_vocabulary()

    assert {"before", "after", "print"} <= vocabulary.unportable_variants
    assert not {"hover", "dark", "focus-visible"} & vocabulary.unportable_variants, (
        "what a touch renderer has no use for loses nothing"
    )
    assert {"float", "columns", "blur", "grid-cols"} <= set(
        vocabulary.unportable_utilities
    )
    assert not {"cursor", "space-x", "space-y"} & set(vocabulary.unportable_utilities)
    from arkitekt_spec.declare.blok.validate import unportable_class

    assert (
        unportable_class("w-[12rem]", vocabulary) is None
        and unportable_class("md:w-48", vocabulary) is None
    )
    assert (
        unportable_class("-mt-[4px]", vocabulary) is None
        and unportable_class("bg-[#1e293b]", vocabulary) is None
    )
    assert (
        unportable_class("translate-x-[3px]", vocabulary)
        == "an arbitrary value of translate-x"
    )
    assert (
        unportable_class("grid-cols-[1fr_2fr]", vocabulary)
        == "an arbitrary value of grid-cols"
    )


def test_functions_live_in_namespaces_with_effects_flagged() -> None:
    functions = load_vocabulary().functions

    assert all("." in name for name in functions)
    assert functions["ui.copy"].effect and functions["task.answer"].effect
    assert functions["query.refetch"].effect and functions["query.fetch_more"].effect
    assert functions["query.fetch_more"].keys == ("query", "variables") and functions[
        "query.fetch_more"
    ].required_keys == ("query",)
    assert not functions["str.upper"].effect
    assert functions["logic.choose"].lazy and functions["logic.all"].lazy
    assert not {"fmt.relative_time", "time.since", "time.until"} & set(functions), (
        "nothing reads the wall clock"
    )
    assert functions["time.diff"].keys == ("start", "end")
    assert functions["val.status"].sees_status
    assert all(function.null for function in functions.values()), (
        "every function says what it does with null"
    )
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

    assert (finding.code, finding.level, finding.path) == (
        "roots.unknown",
        "error",
        "Card[0]",
    )
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

    assert theme.spectral_roles == tuple(
        f"spectrum-{band}"
        for band in ("violet", "blue", "cyan", "green", "yellow", "orange", "red")
    )
    for role in theme.spectral_roles:
        assert (
            role in theme.colour_roles
            and f"{role}-soft" in theme.colour_roles
            and role in theme.logo_pens
        )


def test_a_runtime_code_is_no_checkers() -> None:
    diagnostics = load_vocabulary().diagnostics

    assert diagnostics["deps.unbound"].runtime
    assert not any(
        spec.runtime for code, spec in diagnostics.items() if code != "deps.unbound"
    )
    assert not {
        "catalog.conflict",
        "catalog.shadow",
        "catalog.undeclared",
        "module.unknown",
        "space.kind",
    } & set(diagnostics)
    assert (
        diagnostics["catalog.target"].level
        == diagnostics["catalog.kind"].level
        == "error"
    )
    assert {
        diagnostics[code].level
        for code in (
            "catalog.unknown",
            "part.conditional",
            "chrome.no_ask",
            "class.not_portable",
        )
    } == {"warning"}


def test_a_hint_is_filled_like_its_message() -> None:
    finding = Finding.of(
        "catalog.value",
        "Blok[0]/Button[0]",
        component="Button",
        key="variant",
        value="loud",
        values="default, outline",
    )

    assert finding.hint == "Write one of default, outline."
    assert finding.fields["value"] == "loud"
    assert finding == Finding.of(
        "catalog.value",
        "Blok[0]/Button[0]",
        component="Button",
        key="variant",
        value="loud",
        values="default, outline",
    ).placed(3, 5)


def test_a_finding_of_a_parsed_source_says_where_it_is() -> None:
    from arkitekt_spec.declare.blok.validate import validate_bsx

    (finding,) = validate_bsx(
        '<Blok kind="agent" context="panel">\n  <Card>\n    <Button variant="loud">Go</Button>\n  </Card>\n</Blok>'
    )

    assert (finding.code, finding.line, finding.column) == ("catalog.value", 3, 5)
    with pytest.raises(BlokError) as refused:
        validate_bsx('<Blok kind="agent" context="panel"><Card>')
    assert (refused.value.code, refused.value.line) == ("bsx.unclosed", 1)
    assert (
        refused.value.hint
        == "Close it with </Card>, or write it self-closing as <Card ... />."
    )


def test_every_hint_fills_from_its_message_fields() -> None:
    import string

    for code, spec in load_vocabulary().diagnostics.items():
        if spec.hint is None:
            continue
        message_fields = {
            name for _, name, _, _ in string.Formatter().parse(spec.message) if name
        }
        hint_fields = {
            name for _, name, _, _ in string.Formatter().parse(spec.hint) if name
        }
        assert hint_fields <= message_fields, code
