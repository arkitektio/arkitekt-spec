"""The blok language as a model reads it: a system prompt made from the vocabulary and the catalogs.

What a model is told about components, props and their values, records, roots and functions is
generated from the files the parser, the validator and the renderers load, so it cannot drift from
them (OpenUI's ``library.prompt()``). Only the syntax prose is written by hand, here, beside the
language it describes::

    >>> prompt = blok_prompt(kind="custom")
    >>> "Button[label](" in prompt
    True

:func:`component_signature` is one component's line, for a tool that describes a component on
request instead of listing the whole catalog.
"""

import json
from collections.abc import Iterable, Mapping, Sequence

from arkitekt_spec.blok.bsx import QUERY_SUGAR
from arkitekt_spec.blok.catalogs import BlokView, default_view
from arkitekt_spec.blok.vocabulary import JSON, Function, load_vocabulary, vocabulary_text
from arkitekt_spec.declare.catalogs import CALLBACK_KIND, ComponentSpec

UNIVERSAL_PROPS = ("className",)
"""Props nearly every HTML component takes: said once, left out of the signatures."""

SYNTAX = """\
A blok is written in BSX (blok language v{vocab}), which reads like JSX:
- An element is a catalog component (names are case-sensitive) or a language element \
(`if`, `else`, `foreach`, `match`, `suspense`, `fragment`). The root names the blok's context: \
{roots}.
- `attr="..."` is always a literal string. `attr={{...}}` is an expression: numbers and \
booleans are written as expressions (min={{1}}, fullWidth={{true}}). A prop listed with \
quoted values takes only those.
- Text goes between tags when the component has a text prop (the `[prop]` after its name \
below): <Text>Hello</Text>, <Button>Move</Button>, <Text>T = {{deps.stage.environment.temperature}} °C</Text>.
- Expressions read paths: `deps.<key>.<state>.<field>` (a dependency's state), \
`deps.<key>.tasks.<action>` (the latest task of one of its actions), a bare local (`x`), \
`args.<key>` (the item the blok is opened for), and records named with `into`. Operators: \
== != > >= < <=, && || !, `a ?? b` (the first that is not null: \
{{deps.stage.position.label ?? "unnamed"}}) and the ternary c ? a : b. Literals are JSON \
("text", 3, true, null, [..], {{k: ..}}). There is no `?.`, no arithmetic (use math.*), no subscripts.
- Locals are the blok's form state: declared in `locals`, written only by `bind` on a control \
(<Slider bind={{x}} />), read bare. Every local you bind or read must be declared, but its kind \
is usually told by the blok, so leave it out: a default gives it (0 is an INT, 0.5 a FLOAT, "" \
a STRING, false a BOOL), else the task argument it is passed to whole \
(deps.stage.move(x: x) gives `x` that argument's port), else the control bound to it (a \
Switch a BOOL, an Input a STRING, a Slider a FLOAT); nothing tells, it is ANY (an untyped \
value, never an error). Give a kind only to say more than that \
({{"kind": "STRUCTURE", "identifier": "@mikro/image", "nullable": true}}). A control writes its \
own kind and a call argument built from a local must take the local's kind.
- A callback does exactly one thing: one task call, or one effect (listed under Functions). \
A call names every argument: onClick={{deps.stage.move(x: x) into run}}. \
Inside a callback, `event.<field>` is what triggered it (the fields are listed per callback prop). \
Use the action's own argument names exactly. No sequences, no assignments.
- `into name` keeps the call's task as a task record (fields below): run.running, run.progress, \
run.returns.<port>. A name belongs to exactly one call; derive state from it \
(`variant={{run.args.position == p.id ? "default" : "outline"}}`) instead of keeping your own.
- Functions live in namespaces, snake_case, named arguments: fmt.number(value: x, digits: 1), \
str.template(template: "{{0}} of {{1}}", values: [a, b]), math.max(values: [a, b]).
- <foreach items={{deps.stage.position.history}} let="p" key={{p.id}}> repeats its children; \
inside, read p.field. Key a list of objects by a stable field; a list of plain values \
(numbers, strings) needs no key: <foreach items={{deps.stage.position.ticks}} let="t">.
- <if when={{run.failed}}>...<else>...</else></if> renders one branch; `when={{...}}` on any \
component guards that one component.
- Nothing ticks in an expression: there is no `now`. `then` is null while the viewer is live \
and the moment they look at while they travel back in time. Show a time with \
<Time value={{deps.stage.position.updated_at}} relative /> ("3 minutes ago", ticking by itself) \
or format="HH:mm"; time.diff(start: a, end: b) is the seconds between two times and \
fmt.duration(seconds: s) writes them out.
- Colours are theme roles ({colours}, with opacity like border-info/30), never palette \
colours (bg-green-500) or hex. Conditional classes: className={{["px-2", run.running && "animate-pulse"]}}.
- A dependency is an agent the blok reads and calls under a key: deps.<key>.<slot>, where a \
slot is one of its actions (called) or states (read). Declare every dependency you read, with \
what you read and call of it. One no agent is bound to reads as failed (error deps.unbound) and \
its calls are disabled: show that state (<if when={{deps.stage.tasks.move.failed}}>).\
"""


def _raw() -> Mapping[str, JSON]:
    raw: JSON = json.loads(vocabulary_text())
    assert isinstance(raw, dict)
    return raw


def _first_sentence(text: str | None) -> str:
    if not text:
        return ""
    head, dot, _ = text.partition(". ")
    return head + ("." if dot else "")


def syntax() -> str:
    """The hand-written part: how BSX reads, with the roots and colour roles filled in."""
    vocabulary = load_vocabulary()
    roots = ", ".join(f"<{root}> ({context.lower()})" for root, context in vocabulary.blok_roots.items())
    colours = " ".join(f"bg-{role}" for role in vocabulary.theme.status_roles[:3]) or "bg-muted"
    return SYNTAX.format(vocab=vocabulary.vocab, roots=roots, colours=f"bg-muted, text-muted-foreground, {colours}")


def roots_text(kind: str) -> str:
    """The roots a blok of ``kind`` may read, each with what it is."""
    vocabulary = load_vocabulary()
    allowed = vocabulary.kinds[kind].roots
    roots = _raw()["roots"]
    assert isinstance(roots, dict)
    data, scoped = roots.get("data"), roots.get("scoped")
    assert isinstance(data, dict) and isinstance(scoped, dict)
    lines = [f"Roots a {kind} blok reads ({vocabulary.kinds[kind].description.rstrip('.')}):"]
    lines += [f"- {root}: {description}" for root, description in data.items() if root in allowed]
    lines += [f"- {root}: {description}" for root, description in scoped.items()]
    return "\n".join(lines)


def _record_fields(fields: JSON, prefix: str = "") -> list[str]:
    assert isinstance(fields, list)
    out: list[str] = []
    for field in fields:
        assert isinstance(field, dict)
        key, kind = field["key"], field["kind"]
        choices = field.get("choices")
        shape = "|".join(str(choice) for choice in choices) if isinstance(choices, list) else str(kind)
        nullable = " or null" if field.get("nullable") else ""
        description = field.get("description")
        out.append(f"{prefix}{key}: {shape}{nullable}" + (f" ({description})" if description else ""))
        if field.get("children"):
            out += _record_fields(field["children"], prefix=f"{prefix}{key}.")
    return out


def records_text() -> str:
    """Every record shape (the task record, a query's, ``viewer``, ...), field by field."""
    records = _raw()["records"]
    assert isinstance(records, dict)
    blocks: list[str] = []
    for name, record in records.items():
        assert isinstance(record, dict)
        description = record.get("description")
        blocks.append(f"The {name} record" + (f" ({description})" if description else "") + ": " + "; ".join(_record_fields(record["fields"])))
    return "\n".join(blocks)


def _prop(prop_key: str, kind: str, values: Sequence[str] | None, event: Sequence[str] | None, required: bool) -> str:
    mark = "!" if required else ""
    if values:
        return f"{prop_key}{mark}: " + "|".join(f'"{value}"' for value in values)
    if kind == CALLBACK_KIND and event:
        return f"{prop_key}{mark}: CALLBACK(" + ", ".join(f"event.{key}" for key in event) + ")"
    return f"{prop_key}{mark}: {kind}"


def component_signature(component: ComponentSpec, *, skip: Iterable[str] = UNIVERSAL_PROPS) -> str:
    """One component as a line: ``Button[label](variant: "default"|"outline", onClick: CALLBACK) · children — A clickable button.``

    ``[prop]`` is the prop text children fill, ``!`` marks a required prop, quoted values are the
    only ones the prop takes, ``CALLBACK(event.x)`` names the fields its callback reads.
    """
    skipped = set(skip)
    props = ", ".join(
        _prop(
            prop.key,
            prop.kind,
            prop.values,
            [field.key for field in prop.event] if prop.event else None,
            prop.required,
        )
        for prop in component.props
        if prop.key not in skipped
    )
    text = f"[{component.text_prop}]" if component.text_prop else ""
    tail = []
    if component.accepts_children:
        tail.append("children")
    if component.downstream_of:
        tail.append("inside " + "|".join(component.downstream_of))
    if component.not_downstream_of:
        tail.append("never inside " + "|".join(component.not_downstream_of))
    line = f"{component.name}{text}({props})"
    if tail:
        line += " · " + ", ".join(tail)
    description = _first_sentence(component.description)
    return f"{line} — {description}" if description else line


def drawn_in(view: BlokView, context: str) -> list[ComponentSpec]:
    """The components a blok drawn in ``context`` may place at its top level, in catalog order."""
    return [component for component in view.components.values() if component.inherits_context or component.context == context]


def components_text(view: BlokView, context: str = "HTML", names: Iterable[str] | None = None) -> str:
    """The signature lines of the components ``names`` (default: every one drawn in ``context``),
    and of those their children are drawn in when they bridge contexts (``<Scene>``)."""
    if names is not None:
        chosen = [component for name in names if (component := view.component(name)) is not None]
    else:
        chosen = drawn_in(view, context)
    lines = [
        (
            "Components, as Name[text prop](props) — `!` required, quoted values the only ones a prop takes, "
            f"CALLBACK(event.x) the event its callback reads. Every HTML component also takes {', '.join(UNIVERSAL_PROPS)} (Tailwind classes):"
        )
    ]
    lines += [f"- {component_signature(component)}" for component in chosen]
    return "\n".join(lines)


def function_signature(function: Function) -> str:
    """``fmt.number(value: FLOAT, digits?: INT) -> STRING — …``: ``?`` marks an optional parameter."""
    params = ", ".join(f"{param.key}{'' if param.required else '?'}: {param.kind}" for param in function.params)
    line = f"{function.name}({params}) -> {function.returns}"
    return f"{line} — {function.description}" if function.description else line


def effect_signature(function: Function) -> str:
    """An effect as an author writes it: a query's are its verbs (``queries.<name>.refetch()``)."""
    verb = next((verb for verb, name in QUERY_SUGAR.items() if name == function.name), None)
    if verb is None:
        return function_signature(function)
    params = ", ".join(f"{param.key}: ..." for param in function.params if param.key not in ("query", "variables"))
    variables = any(param.key == "variables" for param in function.params)
    line = f"queries.<name>.{verb}({params or ('<variable>: value, ...' if variables else '')})"
    return f"{line} — {function.description}" if function.description else line


def functions_text(view: BlokView, kind: str | None = None) -> str:
    """The functions an expression may call, then the effects a callback of a ``kind`` blok may hold
    (``task.answer`` only where the blok reads a task)."""
    roots = load_vocabulary().kinds[kind].roots if kind is not None else None
    pure = [function for function in view.functions.values() if not function.effect]
    effects = [
        function
        for function in view.functions.values()
        if function.effect and (roots is None or not function.task_operation or "task" in roots)
    ]
    lines = ["Functions (call with named arguments, `?` optional):"]
    lines += [f"- {function_signature(function)}" for function in pure]
    if effects:
        lines.append("Effects (only as a callback's one thing):")
        lines += [f"- {effect_signature(function)}" for function in effects]
    return "\n".join(lines)


def blok_prompt(
    view: BlokView | None = None,
    *,
    kind: str = "custom",
    context: str = "HTML",
    components: Iterable[str] | None = None,
    examples: Sequence[str] = (),
) -> str:
    """Everything a model needs to write a blok of ``kind`` drawn in ``context``.

    Args:
        view: The catalogs in force; the Orkestrator's default view when None.
        kind: The blok kind (``agent``, ``custom``, ``task``, ``action``): which roots it reads.
        context: Where it is drawn (``HTML``, ``SCENE``, ...): which components are listed.
        components: Only these components' signatures (a short prompt, with a tool that
            describes the rest on request); every one drawn in ``context`` when None.
        examples: Sources shown as they are; the caller checks they validate.
    """
    resolved = view or default_view()
    sections = [syntax(), roots_text(kind), records_text(), components_text(resolved, context, components), functions_text(resolved, kind)]
    for example in examples:
        sections.append(f"An example:\n```xml\n{example}\n```")
    return "\n\n".join(sections)
