"""Print a stored v2 tree as BSX: the inverse of :func:`~arkitekt_spec.blok.bsx.parse_bsx`.

The contract is ``parse_bsx(to_bsx(tree)) == tree`` (with the same view). A tree the parser
could not have produced is refused rather than approximated: text that re-parses into a
different tree changes the blok silently on the next save. Operators are printed as
operators, ``str.template`` text and implicit ``Text`` nodes as text children, a
``className`` list or object as written.
"""

import json
import math
import re
from collections.abc import Sequence

from arkitekt_spec.actions import (
    ActionArgumentInput,
    AgentProbeInput,
    ComponentNodeInput,
    ComponentPropInput,
    UtilCallInput,
)
from arkitekt_spec.blok.bsx import (
    CLASS_NAME,
    CLASSES_FUNCTION,
    FETCH_MORE_FUNCTION,
    FOREACH,
    IMPLICIT_TEXTS,
    REFETCH_FUNCTION,
    TEMPLATE_FUNCTION,
)
from arkitekt_spec.blok.catalogs import BASE_NAME, BlokView, default_view
from arkitekt_spec.blok.vocabulary import JSON, load_vocabulary
from arkitekt_spec.declare.catalogs import ComponentSpec

LINE_WIDTH = 100
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_IMPLICIT = re.compile(r"(?P<parent>.+)/#(?P<index>[0-9]+)")
_PLACEHOLDER = re.compile(r"\{\{|\}\}|\{([0-9]+)\}|[{}]")

# Precedence: higher binds tighter.
_TERNARY, _COALESCE, _OR, _AND, _EQUALITY, _RELATION, _NOT, _ATOM = range(1, 9)
_LOGICAL = {"logic.all": (_AND, " && "), "logic.any": (_OR, " || "), "logic.coalesce": (_COALESCE, " ?? ")}
_COMPARISONS = {"cmp.eq": "==", "cmp.ne": "!=", "cmp.gt": ">", "cmp.gte": ">=", "cmp.lt": "<", "cmp.lte": "<="}


class _Printer:
    def __init__(self, view: BlokView, indent: int) -> None:
        self.view = view
        self.indent = indent
        self.vocabulary = load_vocabulary()

    # -- nodes

    def node(self, node: ComponentNodeInput, derived: str, depth: int, context: str, lines: list[str]) -> None:
        pad = " " * (self.indent * depth)
        spec = self.view.component(node.component)
        inner = context
        if spec is not None:
            inner = context if spec.inherits_context else (spec.children_context or spec.context)
        attributes: list[str] = []
        if node.id != derived:
            if not self.vocabulary.is_explicit_id(node.id):
                raise ValueError(f"<{node.component}>: the id {node.id!r} is neither explicit nor the one its place derives ({derived!r})")
            attributes.append(f"id={_quoted(node.id)}")
        children = list(node.children or ())
        elements = [child for child in children if not self.is_implicit(child, node.id)]
        text_child: str | None = None
        text_prop = spec.text_prop if spec is not None else None
        draws_text = self.vocabulary.contexts[inner].text if inner in self.vocabulary.contexts else True
        props = node.props or ()
        for position, prop in enumerate(props):
            # The parser puts the prop text children fill last, so only the last is spelled as text.
            last = position == len(props) - 1
            if text_prop is not None and prop.key == text_prop and last and not children and draws_text:
                printed = self.text_of(prop, node)
                if printed is not None:
                    text_child = printed
                    continue
            attributes.append(self.attribute(node, prop))
        head = f"<{node.component}{''.join(' ' + a for a in attributes)}"
        wrapped = len(pad) + len(head) > LINE_WIDTH and len(attributes) > 1
        if wrapped:
            head = f"<{node.component}\n" + "\n".join(f"{pad}{' ' * self.indent}{a}" for a in attributes) + f"\n{pad}"
        if text_child is not None:
            lines.append(f"{pad}{head}>{text_child}</{node.component}>")
            return
        if not children:
            lines.append(f"{pad}{head}/>" if wrapped else f"{pad}{head} />")
            return
        lines.append(f"{pad}{head}>")
        counts: dict[str, int] = {}
        implicit = 0
        for child in children:
            if self.is_implicit(child, node.id):
                if not elements and not self.language(spec):
                    raise ValueError(f"<{node.component}> ({node.id}) holds implicit Text with no element beside it")
                if child.id != f"{node.id}/#{implicit}":
                    raise ValueError(f"<{node.component}> ({node.id}): implicit Text {child.id!r} is out of order")
                implicit += 1
                text = self.text_of((child.props or ())[0], child, inline=False) if child.props and len(child.props) == 1 else None
                if text is None:
                    raise ValueError(f"implicit Text {child.id!r} cannot be spelled as text")
                lines.append(f"{pad}{' ' * self.indent}{text}")
                continue
            index = counts.get(child.component, 0)
            counts[child.component] = index + 1
            self.node(child, f"{node.id}/{child.component}[{index}]", depth + 1, inner, lines)
        lines.append(f"{pad}</{node.component}>")

    @staticmethod
    def language(spec: ComponentSpec | None) -> bool:
        return (
            spec is not None
            and spec.catalog is not None
            and spec.catalog.startswith(f"{BASE_NAME}@")
            and spec.inherits_context
            and spec.accepts_children
        )

    @staticmethod
    def is_implicit(child: ComponentNodeInput, parent: str) -> bool:
        match = _IMPLICIT.fullmatch(child.id)
        return match is not None and match.group("parent") == parent and child.component in IMPLICIT_TEXTS

    # -- text

    def text_of(self, prop: ComponentPropInput, node: ComponentNodeInput, *, inline: bool = True) -> str | None:
        """``prop`` as text children, or None when only an attribute spells it.

        ``inline``: printed on the line of its tags, where JSX keeps the spaces at its ends;
        on a line of its own they are trimmed, so they are spelled as strings.
        """
        if prop.key != "text" and node.component in IMPLICIT_TEXTS:
            return None
        if prop.declares_value or prop.agent_call is not None:
            return None
        if isinstance(prop.static_value, str):
            return _text_literal(prop.static_value, edges=not inline)
        if prop.dynamic_value is not None:
            if prop.dynamic_value.literal is not None or prop.dynamic_value.path is None or prop.static_value is not None:
                return None
            return "{" + prop.dynamic_value.path + "}"
        call = prop.util_call
        if call is not None and call.operation == TEMPLATE_FUNCTION:
            parts = _template_parts(call)
            if parts is None:
                return "{" + self.expression_of_call(call, 0) + "}"
            out: list[str] = []
            for index, part in enumerate(parts):
                edge = not inline and index in (0, len(parts) - 1)
                out.append(_text_literal(part, edges=edge) if isinstance(part, str) else "{" + self.argument(part, 0) + "}")
            return "".join(out)
        if call is not None:
            return "{" + self.expression_of_call(call, 0) + "}"
        if isinstance(prop.static_value, (int, float)) and not isinstance(prop.static_value, bool):
            return "{" + _literal(prop.static_value) + "}"
        return None

    # -- attributes

    def attribute(self, node: ComponentNodeInput, prop: ComponentPropInput) -> str:
        key = prop.key
        if prop.agent_call is not None:
            into = f" into {prop.declares_value}" if prop.declares_value else ""
            return f"{key}={{{self.agent_call(prop.agent_call)}{into}}}"
        if prop.declares_value is not None:
            if node.component == FOREACH and key == "let" and prop.static_value == prop.declares_value:
                return f"{key}={_quoted(prop.declares_value)}"
            raise ValueError(f"<{node.component}> ({node.id}): {key} declares {prop.declares_value!r}, which BSX cannot spell")
        if prop.util_call is not None:
            call = prop.util_call
            sugar = self.query_sugar(call)
            if sugar is not None:
                return f"{key}={{{sugar}}}"
            if key == CLASS_NAME and call.operation == CLASSES_FUNCTION and call.arguments and len(call.arguments) == 1:
                values = call.arguments[0]
                if values.key == "values" and (values.value_list is not None or values.value_dict is not None):
                    return f"{key}={{{self.argument(values, 0)}}}"
            return f"{key}={{{self.expression_of_call(call, 0)}}}"
        if prop.dynamic_value is not None:
            if prop.dynamic_value.literal is not None or prop.dynamic_value.path is None:
                raise ValueError(f"<{node.component}> ({node.id}): {key} has a fallback literal, which BSX cannot spell")
            return f"{key}={{{prop.dynamic_value.path}}}"
        value = prop.static_value
        if value is True:
            return key
        if isinstance(value, str):
            return f"{key}={_quoted(value)}"
        if isinstance(value, (list, dict)) and key == CLASS_NAME:
            raise ValueError(f"<{node.component}> ({node.id}): a static className list reads back as str.classes")
        return f"{key}={{{_literal(value)}}}"

    # -- expressions

    def query_sugar(self, call: UtilCallInput) -> str | None:
        """``query.refetch`` / ``query.fetch_more`` as BSX writes them (``queries.<name>.refetch()``),
        or None when the call is not one the sugar spells (a query named by an expression)."""
        if call.operation not in (REFETCH_FUNCTION, FETCH_MORE_FUNCTION):
            return None
        arguments = call.arguments or ()
        if not arguments or arguments[0].key != "query":
            return None
        named = arguments[0]
        if not isinstance(named.value_literal, str) or not _IDENTIFIER.fullmatch(named.value_literal):
            return None
        if any(value is not None for value in (named.value_path, named.util_call, named.agent_call, named.value_list, named.value_dict)):
            return None
        if call.operation == REFETCH_FUNCTION:
            return f"queries.{named.value_literal}.refetch()" if len(arguments) == 1 else None
        variables: list[str] = []
        if len(arguments) > 2:
            return None
        if len(arguments) == 2:
            given = arguments[1]
            if given.key != "variables" or not given.value_dict:
                return None
            for variable in given.value_dict:
                if variable.key is None or not _IDENTIFIER.fullmatch(variable.key):
                    return None
                variables.append(f"{variable.key}: {self.argument(variable, 0)}")
        return f"queries.{named.value_literal}.fetch_more({', '.join(variables)})"

    def agent_call(self, call: AgentProbeInput) -> str:
        callee = f"self.{call.operation}" if call.dependency == "self" else f"deps.{call.dependency}.{call.operation}"
        arguments: list[str] = []
        for argument in call.arguments or ():
            if argument.key is None or not _IDENTIFIER.fullmatch(argument.key):
                raise ValueError(f"{callee}: a task call names its arguments, not {argument.key!r}")
            arguments.append(f"{argument.key}: {self.argument(argument, 0)}")
        return f"{callee}({', '.join(arguments)})"

    def expression_of_call(self, call: UtilCallInput, needed: int) -> str:
        text, precedence = self.call(call)
        return f"({text})" if precedence < needed else text

    def call(self, call: UtilCallInput) -> tuple[str, int]:
        arguments = {argument.key: argument for argument in call.arguments or ()}
        keys = [argument.key for argument in call.arguments or ()]
        operation = call.operation
        if operation in _COMPARISONS and keys == ["a", "b"]:
            needed = _RELATION + 1 if operation in ("cmp.eq", "cmp.ne") else _NOT
            return f"{self.argument(arguments['a'], needed)} {_COMPARISONS[operation]} {self.argument(arguments['b'], needed)}", (
                _EQUALITY if operation in ("cmp.eq", "cmp.ne") else _RELATION
            )
        if operation in _LOGICAL and keys == ["values"]:
            values = arguments["values"].value_list
            if values is not None and len(values) >= 2:
                own, joiner = _LOGICAL[operation]
                return joiner.join(self.argument(value, own + 1) for value in values), own
        if operation == "logic.negate" and keys == ["a"]:
            return "!" + self.argument(arguments["a"], _NOT), _NOT
        if operation == "logic.choose" and keys == ["cond", "then", "otherwise"]:
            return (
                f"{self.argument(arguments['cond'], _COALESCE)} ? {self.argument(arguments['then'], 0)} : {self.argument(arguments['otherwise'], 0)}",
                _TERNARY,
            )
        function = self.view.functions.get(operation)
        printed: list[str] = []
        if function is None:
            positional = sorted((a for a in call.arguments or () if a.key is not None and a.key.isdigit()), key=lambda a: int(a.key or 0))
            if [a.key for a in positional] != [str(index) for index in range(len(positional))]:
                raise ValueError(f"{operation}: its positional arguments are not numbered 0, 1, ...")
            printed = [self.argument(a, 0) for a in positional]
            printed += [f"{a.key}: {self.argument(a, 0)}" for a in call.arguments or () if a.key is not None and not a.key.isdigit()]
            return f"{operation}({', '.join(printed)})", _ATOM
        params = list(function.keys)
        if params == ["values"] and keys == ["values"]:
            values = arguments["values"]
            if values.value_list is not None and len(values.value_list) >= 2:
                return f"{operation}({', '.join(self.argument(v, 0) for v in values.value_list)})", _ATOM
            return f"{operation}({self.argument(values, 0)})", _ATOM
        if keys == params[: len(keys)]:
            printed = [self.argument(arguments[key], 0) for key in keys]
        else:
            printed = [f"{key}: {self.argument(arguments[key], 0)}" for key in keys if key is not None]
        return f"{operation}({', '.join(printed)})", _ATOM

    def argument(self, argument: ActionArgumentInput, needed: int) -> str:
        if argument.agent_call is not None:
            raise ValueError("a task call is a callback's one thing, never an argument")
        if argument.value_path is not None:
            return argument.value_path
        if argument.util_call is not None:
            return self.expression_of_call(argument.util_call, needed)
        if argument.value_list is not None:
            return "[" + ", ".join(self.argument(item, 0) for item in argument.value_list) + "]"
        if argument.value_dict is not None:
            entries: list[str] = []
            for entry in argument.value_dict:
                if not entry.key:
                    raise ValueError("an object's key is a non-empty string")
                name = entry.key if _IDENTIFIER.fullmatch(entry.key) else json.dumps(entry.key, ensure_ascii=False)
                entries.append(f"{name}: {self.argument(entry, 0)}")
            return "{" + ", ".join(entries) + "}"
        return _literal(argument.value_literal)


def _template_parts(call: UtilCallInput) -> list[str | ActionArgumentInput] | None:
    """The parts a ``str.template`` call was parsed from, or None when no text spells it."""
    arguments = {argument.key: argument for argument in call.arguments or ()}
    template = arguments.get("template")
    values = arguments.get("values")
    if template is None or not isinstance(template.value_literal, str) or values is None or values.value_list is None:
        return None
    items = list(values.value_list)
    parts: list[str | ActionArgumentInput] = []
    literal: list[str] = []
    expected = 0
    position = 0
    text = template.value_literal
    for match in _PLACEHOLDER.finditer(text):
        literal.append(text[position : match.start()])
        position = match.end()
        token = match.group(0)
        if token in ("{{", "}}"):
            literal.append(token[0])
            continue
        if match.group(1) is None or int(match.group(1)) != expected or expected >= len(items):
            return None
        if "".join(literal):
            parts.append("".join(literal))
        literal = []
        parts.append(items[expected])
        expected += 1
    literal.append(text[position:])
    if "".join(literal):
        parts.append("".join(literal))
    if expected != len(items):
        return None
    return parts


def _text_literal(text: str, *, edges: bool) -> str:
    """A literal run of text: raw when the parser reads it back unchanged, a string expression
    otherwise; with ``edges``, spaces at its ends would be trimmed too."""
    unsafe = (
        not text
        or (edges and text != text.strip(" "))
        or "  " in text
        or any(char in text for char in "\n\r\t{}<>")
    )
    if unsafe:
        return "{" + json.dumps(text, ensure_ascii=False) + "}"
    return text.replace("&", "&amp;")


def _quoted(value: str) -> str:
    """An attribute string, in the quotes that need no escaping when there are such."""
    escaped = value.replace("&", "&amp;").replace("<", "&lt;")
    if '"' in escaped and "'" not in escaped:
        return f"'{escaped}'"
    return '"' + escaped.replace('"', "&quot;") + '"'


def _literal(value: JSON) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{value!r} is not a JSON number")
        return json.dumps(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_literal(item) for item in value) + "]"
    entries = [
        f"{key if _IDENTIFIER.fullmatch(key) else json.dumps(key, ensure_ascii=False)}: {_literal(item)}"
        for key, item in value.items()
    ]
    if any(not key for key in value):
        raise ValueError("an object's key is a non-empty string")
    return "{" + ", ".join(entries) + "}"


def to_bsx(node: ComponentNodeInput, *, view: BlokView | None = None, indent: int = 2) -> str:
    """Print ``node`` as v2 BSX that :func:`parse_bsx` (with the same view) reads back into it.

    Raises:
        ValueError: If the tree holds something BSX cannot spell (an id that is neither
            explicit nor derived, a fallback literal, a declared value other than a let or an
            into, implicit Text where the parser would not make it).
    """
    lines: list[str] = []
    _Printer(view or default_view(), indent).node(node, f"{node.component}[0]", 0, "HTML", lines)
    return "\n".join(lines)


def to_bsx_all(nodes: Sequence[ComponentNodeInput], *, view: BlokView | None = None) -> list[str]:
    """Every root of a blok, printed."""
    return [to_bsx(node, view=view) for node in nodes]


__all__ = ["LINE_WIDTH", "to_bsx", "to_bsx_all"]
