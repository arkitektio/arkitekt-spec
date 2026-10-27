"""BSX v2: a blok's JSX-like source, parsed into the tree that is stored.

The markup is XML-shaped with JSX's two additions: an attribute or a child may be an
expression in braces, and text children are content::

    <Card>
      <Button onClick={deps.robot.move_to(position: p.id) into move}>Grip</Button>
      <Text>Arrived at {move.args.position}</Text>
    </Card>

- ``attr="..."`` is always a literal string, ``attr={...}`` an expression (:mod:`.expr`), a bare
  ``attr`` is ``true``.
- Text children fill the component's ``text_prop``; mixed with elements they become implicit
  ``Text`` nodes. Several parts desugar to ``str.template``. Whitespace follows JSX.
- ``className={[...]}`` and ``className={{...}}`` desugar to ``str.classes`` (clsx).
- ``<foreach items={...} let="p" key={...}>``: ``key`` is optional; without it items are keyed
  by their position, and the stored node has no ``key`` prop.
- Operators desugar to ``cmp.*`` / ``logic.*`` calls (``a ?? b`` to ``logic.coalesce``); a callback holds one task call (``into
  name`` names its record) or one effect. ``queries.<name>.refetch()`` and
  ``queries.<name>.fetch_more(name: value, ...)`` desugar to the effects ``query.refetch`` and
  ``query.fetch_more``, the query named by a literal.

Only the parser needs a catalog: which prop text fills, and where text cannot be drawn. That
is the one table it takes (:class:`~arkitekt_spec.blok.catalogs.BlokView`); everything else a
catalog says is the validator's.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from arkitekt_spec.actions import (
    ActionArgumentInput,
    AgentProbeInput,
    ComponentNodeInput,
    ComponentPropInput,
    DynamicValueInput,
    UtilCallInput,
)
from arkitekt_spec.blok.catalogs import BASE_NAME, BlokView, default_view
from arkitekt_spec.blok.expr import (
    Call,
    Compare,
    Expr,
    Into,
    ListExpr,
    Literal,
    Locator,
    Logical,
    Not,
    ObjectExpr,
    Path,
    Ternary,
    parse_attribute,
    parse_expression,
)
from arkitekt_spec.blok.vocabulary import JSON, BlokError, load_vocabulary

MAX_SOURCE_BYTES = 256 * 1024
"""The largest source a blok may be."""

IMPLICIT_TEXT = "Text"
#: Loose text inside an ``html@`` component becomes a ``span``, so an html-only blok stays
#: inside the catalogs it declares; everywhere else it becomes a ``Text``.
IMPLICIT_HTML_TEXT = "span"
IMPLICIT_TEXTS = (IMPLICIT_TEXT, IMPLICIT_HTML_TEXT)
"""The component a run of text becomes when it sits among elements."""
CLASS_NAME = "className"
CLASSES_FUNCTION = "str.classes"
TEMPLATE_FUNCTION = "str.template"
ELSE = "else"
IF = "if"
FOREACH = "foreach"
REFETCH_FUNCTION = "query.refetch"
FETCH_MORE_FUNCTION = "query.fetch_more"
QUERY_SUGAR = {"refetch": REFETCH_FUNCTION, "fetch_more": FETCH_MORE_FUNCTION}
"""``queries.<name>.<verb>(...)``: the effect each verb desugars to."""

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.:-]*")
_ATTRIBUTE = re.compile(r"[A-Za-z_:][A-Za-z0-9_.:-]*")
_LOCAL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ENTITY = re.compile(r"&(#[0-9]+|#x[0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]*);")
_ENTITIES = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'", "nbsp": " "}


# --------------------------------------------------------------------------- markup


@dataclass
class _Attribute:
    name: str
    value: str | Expr | bool
    offset: int


@dataclass
class _Text:
    text: str
    offset: int


@dataclass
class _Container:
    expression: Expr
    offset: int


@dataclass
class _Element:
    name: str
    offset: int
    attributes: list[_Attribute] = field(default_factory=list["_Attribute"])
    children: list["_Element | _Text | _Container"] = field(default_factory=list["_Element | _Text | _Container"])


class _Markup:
    """Reads the markup into elements, text and expression containers."""

    def __init__(self, source: str, locate: Locator) -> None:
        self.source = source
        self.locate = locate
        self.index = 0

    def fail(self, code: str, offset: int | None = None, **fields: object) -> BlokError:
        line, column = self.locate(self.index if offset is None else offset)
        return BlokError(code, line=line, column=column, **fields)

    def at(self, text: str) -> bool:
        return self.source.startswith(text, self.index)

    def skip_space(self) -> None:
        while self.index < len(self.source) and self.source[self.index] in " \t\r\n":
            self.index += 1

    def skip_comment(self) -> bool:
        if not self.at("<!--"):
            return False
        end = self.source.find("-->", self.index + 4)
        if end < 0:
            raise self.fail("bsx.syntax", detail="a comment is never closed")
        self.index = end + 3
        return True

    def skip_trivia(self) -> None:
        while True:
            self.skip_space()
            if not self.skip_comment():
                return

    def document(self) -> _Element:
        self.skip_trivia()
        if self.index >= len(self.source):
            raise self.fail("bsx.empty")
        if not self.at("<"):
            raise self.fail("bsx.syntax", detail="a blok starts with an element")
        root = self.element()
        self.skip_trivia()
        if self.index < len(self.source):
            if self.at("<"):
                name = _NAME.match(self.source, self.index + 1)
                raise self.fail("bsx.multiple_roots", component=name.group(0) if name else "?")
            raise self.fail("bsx.syntax", detail="text after the root element")
        return root

    def name(self, pattern: re.Pattern[str], what: str) -> str:
        match = pattern.match(self.source, self.index)
        if match is None:
            raise self.fail("bsx.syntax", detail=f"expected {what}")
        self.index = match.end()
        return match.group(0)

    def element(self) -> _Element:
        offset = self.index
        self.index += 1
        element = _Element(name=self.name(_NAME, "an element name"), offset=offset)
        seen: set[str] = set()
        while True:
            had_space = self.index < len(self.source) and self.source[self.index] in " \t\r\n"
            self.skip_space()
            if self.index >= len(self.source):
                raise self.fail("bsx.unclosed", offset, component=element.name)
            if self.at("/>"):
                self.index += 2
                return element
            if self.at(">"):
                self.index += 1
                self.content(element)
                return element
            if not had_space:
                raise self.fail("bsx.syntax", detail="expected a space before the attribute")
            attribute = self.attribute()
            if attribute.name in seen:
                raise self.fail("bsx.duplicate_attribute", attribute.offset, component=element.name, key=attribute.name)
            seen.add(attribute.name)
            element.attributes.append(attribute)

    def attribute(self) -> _Attribute:
        offset = self.index
        name = self.name(_ATTRIBUTE, "an attribute name")
        save = self.index
        self.skip_space()
        if not self.at("="):
            self.index = save
            return _Attribute(name=name, value=True, offset=offset)
        self.index += 1
        self.skip_space()
        if self.at('"') or self.at("'"):
            quote = self.source[self.index]
            end = self.source.find(quote, self.index + 1)
            if end < 0:
                raise self.fail("bsx.syntax", detail=f"the value of {name} is never closed")
            raw = self.source[self.index + 1 : end]
            value = self.decode(raw, self.index + 1)
            self.index = end + 1
            return _Attribute(name=name, value=value, offset=offset)
        if self.at("{"):
            start, end = self.braces()
            inner = self.source[start:end]
            self.index = end + 1
            if not inner.strip():
                raise self.fail("expr.syntax", start, detail=f"the value of {name} is empty")
            expression = parse_attribute(inner, base=start, locate=self.locate)
            return _Attribute(name=name, value=expression, offset=offset)
        raise self.fail("bsx.syntax", detail=f'{name}= takes "a string" or {{an expression}}')

    def braces(self) -> tuple[int, int]:
        """The span inside the braces opening at the cursor: strings and nested braces skipped."""
        start = self.index + 1
        depth = 1
        index = start
        source = self.source
        while index < len(source):
            char = source[index]
            if char in "\"'":
                index += 1
                while index < len(source) and source[index] != char:
                    if source[index] == "\\":
                        index += 1
                    if source[index] == "\n":
                        break
                    index += 1
                index += 1
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return start, index
            index += 1
        raise self.fail("bsx.syntax", self.index, detail="an expression's braces are never closed")

    def content(self, element: _Element) -> None:
        while True:
            if self.index >= len(self.source):
                raise self.fail("bsx.unclosed", element.offset, component=element.name)
            if self.at("</"):
                offset = self.index
                self.index += 2
                found = self.name(_NAME, "an element name")
                self.skip_space()
                if not self.at(">"):
                    raise self.fail("bsx.syntax", detail=f"expected '>' to close </{found}")
                self.index += 1
                if found != element.name:
                    raise self.fail("bsx.mismatched_tag", offset, found=found, expected=element.name)
                return
            if self.skip_comment():
                continue
            if self.at("<"):
                element.children.append(self.element())
                continue
            if self.at("{"):
                offset = self.index
                start, end = self.braces()
                inner = self.source[start:end]
                self.index = end + 1
                stripped = inner.strip()
                if not stripped or (stripped.startswith("/*") and stripped.endswith("*/")):
                    continue
                element.children.append(_Container(expression=parse_expression(inner, base=start, locate=self.locate), offset=offset))
                continue
            if self.at("}"):
                raise self.fail("bsx.syntax", detail="unexpected '}' in text; write {'}'}")
            offset = self.index
            end = len(self.source)
            for stop in ("<", "{", "}"):
                found_at = self.source.find(stop, self.index)
                if 0 <= found_at < end:
                    end = found_at
            raw = self.source[self.index : end]
            self.index = end
            element.children.append(_Text(text=raw, offset=offset))

    def decode(self, raw: str, offset: int) -> str:
        def replace(match: re.Match[str]) -> str:
            entity = match.group(1)
            if entity.startswith("#x"):
                return chr(int(entity[2:], 16))  # noqa: FURB166 -- an entity's x, not Python's 0x
            if entity.startswith("#"):
                return chr(int(entity[1:]))
            if entity in _ENTITIES:
                return _ENTITIES[entity]
            raise self.fail("bsx.syntax", offset + match.start(), detail=f"unknown entity &{entity};")

        return _ENTITY.sub(replace, raw)


def jsx_text(raw: str) -> str:
    """JSX's whitespace rule for one run of text: lines trimmed where they break, whitespace-only
    lines dropped, the rest joined by one space; runs of spaces in a line collapse to one."""
    lines = re.split(r"\r\n|\n|\r", raw)
    kept: list[str] = []
    for index, line in enumerate(lines):
        text = line.replace("\t", " ")
        if index > 0:
            text = text.lstrip(" ")
        if index < len(lines) - 1:
            text = text.rstrip(" ")
        text = re.sub(" {2,}", " ", text)
        if text:
            kept.append(text)
    return " ".join(kept)


# --------------------------------------------------------------------------- lowering


Part = str | Expr


class _Lowering:
    """Turns markup into the stored tree, with the view's text table at hand."""

    def __init__(self, view: BlokView, locate: Locator, markup: _Markup) -> None:
        self.view = view
        self.locate = locate
        self.markup = markup
        self.vocabulary = load_vocabulary()
        self.ids: set[str] = set()
        self.spans: dict[str, tuple[int, int]] = {}
        """Per node id, the line and column (1-based) its element starts at."""

    def fail(self, code: str, offset: int, **fields: object) -> BlokError:
        line, column = self.locate(offset)
        return BlokError(code, line=line, column=column, **fields)

    # -- nodes

    def node(self, element: _Element, node_id: str, context: str, parent: str | None, loose_text: str = IMPLICIT_TEXT) -> ComponentNodeInput:
        """``loose_text``: the component loose text becomes here, from the nearest non-language ancestor."""
        spec = self.view.component(element.name)
        if spec is not None and spec.catalog is not None and spec.catalog.startswith("html@"):
            loose_text = IMPLICIT_HTML_TEXT
        elif spec is not None and not (spec.catalog is not None and spec.catalog.startswith(f"{BASE_NAME}@")):
            loose_text = IMPLICIT_TEXT
        if element.name == ELSE and parent != IF:
            raise self.fail("bsx.else_outside_if", element.offset)
        explicit = next((a for a in element.attributes if a.name == "id"), None)
        if explicit is not None:
            if not isinstance(explicit.value, str) or not self.vocabulary.is_explicit_id(explicit.value):
                written = explicit.value if isinstance(explicit.value, str) else "{...}"
                raise self.fail("bsx.id", explicit.offset, id=written)
            if explicit.value in self.ids:
                raise self.fail("bsx.duplicate_id", explicit.offset, id=explicit.value)
            self.ids.add(explicit.value)
            node_id = explicit.value

        props = [self.prop(element.name, attribute) for attribute in element.attributes if attribute.name != "id"]
        if element.name == FOREACH:
            self.check_foreach(element)

        inner = context
        if spec is not None:
            inner = context if spec.inherits_context else (spec.children_context or spec.context)

        children: list[ComponentNodeInput] = []
        runs = self.runs(element)
        has_elements = any(isinstance(child, _Element) for child in element.children)
        text_runs = [run for run in runs if isinstance(run, list)]
        implicit_text = False
        if text_runs:
            text_prop = self.text_target(element, inner, text_runs, has_elements)
            implicit_text = text_prop is None
            if text_prop is not None:
                if any(prop.key == text_prop for prop in props):
                    raise self.fail("bsx.text_and_prop", element.offset, component=element.name, key=text_prop)
                props.append(self.text_prop(text_prop, text_runs[0], element.offset))
        implicit = 0
        counts: dict[str, int] = {}
        seen_else = False
        for run in runs:
            if isinstance(run, list):
                if not implicit_text:
                    continue
                if seen_else:
                    raise self.fail("bsx.else_order", element.offset)
                children.append(
                    ComponentNodeInput(
                        id=f"{node_id}/#{implicit}",
                        component=loose_text,
                        props=(self.text_prop("text", run, element.offset),),
                    )
                )
                implicit += 1
                continue
            child = run
            if element.name == IF:
                if child.name == ELSE:
                    seen_else = True
                elif seen_else:
                    raise self.fail("bsx.else_order", child.offset)
            index = counts.get(child.name, 0)
            counts[child.name] = index + 1
            children.append(self.node(child, f"{node_id}/{child.name}[{index}]", inner, element.name, loose_text))
        if element.name == IF:
            elses = [child for child in element.children if isinstance(child, _Element) and child.name == ELSE]
            for other in elses[:-1]:
                if not any(attribute.name == "when" for attribute in other.attributes):
                    raise self.fail("bsx.else_order", other.offset)

        self.spans[node_id] = self.locate(element.offset)
        for child in children:
            self.spans.setdefault(child.id, self.spans[node_id])
        return ComponentNodeInput(
            id=node_id,
            component=element.name,
            props=tuple(props) if props else None,
            children=tuple(children) if children else None,
        )

    def check_foreach(self, element: _Element) -> None:
        attributes = {attribute.name: attribute for attribute in element.attributes}
        missing = [
            spelled
            for name, spelled in (("items", "items={...}"), ("let", 'let="name"'))
            if name not in attributes
        ]
        if missing:
            raise self.fail("bsx.foreach", element.offset, missing=", ".join(missing))
        let = attributes["let"]
        if not isinstance(let.value, str) or not _LOCAL.fullmatch(let.value):
            raise self.fail("bsx.let", let.offset, let=let.value if isinstance(let.value, str) else "{...}")
        for name in ("items", "key"):
            if name in attributes and isinstance(attributes[name].value, (str, bool)):
                raise self.fail("bsx.foreach", attributes[name].offset, missing=f"{name}={{...}}, an expression")

    def runs(self, element: _Element) -> list["list[Part] | _Element"]:
        """The children as text runs (parts between elements) and elements, in order."""
        runs: list[list[Part] | _Element] = []
        current: list[Part] = []

        def flush() -> None:
            merged: list[Part] = []
            for part in current:
                if isinstance(part, str) and merged and isinstance(merged[-1], str):
                    merged[-1] = merged[-1] + part
                elif not (isinstance(part, str) and not part):
                    merged.append(part)
            if merged:
                runs.append(merged)
            current.clear()

        for child in element.children:
            if isinstance(child, _Element):
                flush()
                runs.append(child)
            elif isinstance(child, _Text):
                text = self.markup.decode(jsx_text(child.text), child.offset)
                if text:
                    current.append(text)
            else:
                expression = child.expression
                if isinstance(expression, Literal) and isinstance(expression.value, str):
                    current.append(expression.value)
                else:
                    self.check_pure(expression, child.offset, text=True)
                    current.append(expression)
        flush()
        return runs

    def text_target(
        self,
        element: _Element,
        context: str,
        runs: Sequence[Sequence[Part]],
        has_elements: bool,
    ) -> str | None:
        """Where text goes: the text_prop (returned), implicit Text nodes (None), or refused."""
        component = self.view.component(element.name)
        sample = self.sample(runs[0])
        if component is None:
            raise self.fail("bsx.text_unknown_component", element.offset, component=element.name, text=sample)
        draws_text = self.vocabulary.contexts.get(context)
        if draws_text is not None and not draws_text.text:
            raise self.fail("bsx.text_in_context", element.offset, component=element.name, context=context, text=sample)
        language = component.catalog is not None and component.catalog.startswith(f"{BASE_NAME}@") and component.inherits_context and component.accepts_children
        if component.text_prop is not None and not has_elements:
            if len(runs) > 1:
                raise self.fail("bsx.text_not_accepted", element.offset, component=element.name, text=sample)
            return component.text_prop
        if component.text_prop is not None or language:
            return None
        raise self.fail("bsx.text_not_accepted", element.offset, component=element.name, text=sample)

    @staticmethod
    def sample(parts: Sequence[Part]) -> str:
        return "".join(part if isinstance(part, str) else "{...}" for part in parts)[:60]

    def text_prop(self, key: str, parts: Sequence[Part], offset: int) -> ComponentPropInput:
        if len(parts) == 1:
            only = parts[0]
            if isinstance(only, str):
                return ComponentPropInput(key=key, static_value=only)
            return self.value(key, only, offset)
        template: list[str] = []
        values: list[ActionArgumentInput] = []
        for part in parts:
            if isinstance(part, str):
                template.append(part.replace("{", "{{").replace("}", "}}"))
            else:
                template.append("{" + str(len(values)) + "}")
                values.append(self.argument(None, part))
        call = UtilCallInput(
            operation=TEMPLATE_FUNCTION,
            arguments=(
                ActionArgumentInput(key="template", value_literal="".join(template)),
                ActionArgumentInput(key="values", value_list=tuple(values)),
            ),
        )
        return ComponentPropInput(key=key, util_call=call)

    # -- props

    def prop(self, component: str, attribute: _Attribute) -> ComponentPropInput:
        value = attribute.value
        if isinstance(value, bool):
            return ComponentPropInput(key=attribute.name, static_value=value)
        if isinstance(value, str):
            if attribute.name == "let" and component == FOREACH:
                return ComponentPropInput(key=attribute.name, static_value=value, declares_value=value)
            return ComponentPropInput(key=attribute.name, static_value=value)
        if attribute.name == "let" and component == FOREACH:
            raise self.fail("bsx.let", attribute.offset, let="{...}")
        return self.value(attribute.name, value, attribute.offset)

    def value(self, key: str, expression: Expr, offset: int) -> ComponentPropInput:
        if isinstance(expression, Into):
            call = self.agent_call(expression.call)
            if call is None:
                raise self.fail("callback.into", expression.offset)
            return ComponentPropInput(key=key, agent_call=call, declares_value=expression.name)
        if isinstance(expression, Literal):
            return ComponentPropInput(key=key, static_value=expression.value)
        if isinstance(expression, (ListExpr, ObjectExpr)):
            if key == CLASS_NAME:
                return ComponentPropInput(
                    key=key,
                    util_call=UtilCallInput(operation=CLASSES_FUNCTION, arguments=(self.argument("values", expression),)),
                )
            literal = self.literal(expression)
            if literal is None:
                raise self.fail("expr.dynamic_container", expression.offset)
            return ComponentPropInput(key=key, static_value=literal[0])
        if isinstance(expression, Path):
            return ComponentPropInput(key=key, dynamic_value=DynamicValueInput(path=expression.dotted))
        if isinstance(expression, Call):
            agent = self.agent_call(expression)
            if agent is not None:
                return ComponentPropInput(key=key, agent_call=agent)
            effect = self.query_effect(expression)
            if effect is not None:
                return ComponentPropInput(key=key, util_call=effect)
            return ComponentPropInput(key=key, util_call=self.util_call(expression, effect_allowed=True))
        return ComponentPropInput(key=key, util_call=self.operator(expression))

    def literal(self, expression: Expr) -> tuple[JSON] | None:
        """The JSON value an all-literal expression is, or None when it holds an expression."""
        if isinstance(expression, Literal):
            return (expression.value,)
        if isinstance(expression, ListExpr):
            items: list[JSON] = []
            for item in expression.items:
                value = self.literal(item)
                if value is None:
                    return None
                items.append(value[0])
            return (items,)
        if isinstance(expression, ObjectExpr):
            entries: dict[str, JSON] = {}
            for name, item in expression.entries:
                value = self.literal(item)
                if value is None:
                    return None
                entries[name] = value[0]
            return (entries,)
        return None

    # -- calls

    def agent_call(self, call: Call) -> AgentProbeInput | None:
        """A task call (``self.<action>(...)``, ``deps.<key>.<action>(...)``), or None."""
        callee = call.callee
        if callee[0] == "self" and len(callee) == 2:
            dependency, operation = "self", callee[1]
        elif callee[0] == "deps" and len(callee) == 3:
            dependency, operation = callee[1], callee[2]
        else:
            return None
        arguments: list[ActionArgumentInput] = []
        seen: set[str] = set()
        for argument in call.arguments:
            if argument.name is None:
                raise self.fail("callback.positional", argument.offset, call=call.name)
            if argument.name in seen:
                raise self.fail("expr.duplicate_key", argument.offset, key=argument.name)
            seen.add(argument.name)
            arguments.append(self.argument(argument.name, argument.value))
        return AgentProbeInput(dependency=dependency, operation=operation, arguments=tuple(arguments) if arguments else None)

    def query_effect(self, call: Call) -> UtilCallInput | None:
        """``queries.<name>.refetch()`` / ``.fetch_more(k: v, ...)`` as its effect, or None."""
        callee = call.callee
        if len(callee) != 3 or callee[2] not in QUERY_SUGAR:
            return None
        if callee[0] == "subscriptions":
            raise self.fail("subscription.refetch", call.offset)
        if callee[0] != "queries":
            return None
        named = ActionArgumentInput(key="query", value_literal=callee[1])
        if callee[2] == "refetch":
            if call.arguments:
                raise self.fail("expr.arguments", call.offset, function=call.name, detail="refetch takes no arguments")
            return UtilCallInput(operation=REFETCH_FUNCTION, arguments=(named,))
        variables: list[ActionArgumentInput] = []
        seen: set[str] = set()
        for argument in call.arguments:
            if argument.name is None:
                raise self.fail("callback.positional", argument.offset, call=call.name)
            if argument.name in seen:
                raise self.fail("expr.duplicate_key", argument.offset, key=argument.name)
            seen.add(argument.name)
            variables.append(self.argument(argument.name, argument.value))
        if not variables:
            return UtilCallInput(operation=FETCH_MORE_FUNCTION, arguments=(named,))
        return UtilCallInput(
            operation=FETCH_MORE_FUNCTION,
            arguments=(named, ActionArgumentInput(key="variables", value_dict=tuple(variables))),
        )

    def util_call(self, call: Call, *, effect_allowed: bool) -> UtilCallInput:
        name = call.name
        vocabulary = self.vocabulary
        function = self.view.functions.get(name)
        if function is None:
            namespace = call.callee[0]
            if namespace not in vocabulary.function_namespaces or len(call.callee) < 2:
                raise self.fail("expr.unknown_callee", call.offset, callee=name)
        elif function.effect and not effect_allowed:
            raise self.fail("callback.nested", call.offset, what=name)
        positional = [argument for argument in call.arguments if argument.name is None]
        named = [argument for argument in call.arguments if argument.name is not None]
        if positional and named and call.arguments.index(named[0]) < call.arguments.index(positional[-1]):
            raise self.fail("expr.arguments", call.offset, function=name, detail="a positional argument follows a named one")
        if function is None:
            arguments = [self.argument(str(index), argument.value) for index, argument in enumerate(positional)]
            arguments += [self.argument(argument.name, argument.value) for argument in named if argument.name is not None]
            return UtilCallInput(operation=name, arguments=tuple(arguments) if arguments else None)
        keys = function.keys
        resolved: dict[str, ActionArgumentInput] = {}
        if len(keys) == 1 and keys[0] == "values" and len(positional) > 1:
            resolved["values"] = ActionArgumentInput(
                key="values", value_list=tuple(self.argument(None, argument.value) for argument in positional)
            )
        else:
            if len(positional) > len(keys):
                raise self.fail(
                    "expr.arguments", call.offset, function=name, detail=f"takes at most {len(keys)} arguments ({', '.join(keys)})"
                )
            for key, argument in zip(keys, positional, strict=False):
                resolved[key] = self.argument(key, argument.value)
        for argument in named:
            assert argument.name is not None
            if argument.name not in keys:
                raise self.fail("expr.arguments", argument.offset, function=name, detail=f"has no argument {argument.name!r} ({', '.join(keys)})")
            if argument.name in resolved:
                raise self.fail("expr.arguments", argument.offset, function=name, detail=f"{argument.name!r} is given twice")
            resolved[argument.name] = self.argument(argument.name, argument.value)
        missing = [key for key in function.required_keys if key not in resolved]
        if missing:
            raise self.fail("expr.arguments", call.offset, function=name, detail=f"needs {', '.join(missing)}")
        ordered = tuple(resolved[key] for key in keys if key in resolved)
        return UtilCallInput(operation=name, arguments=ordered if ordered else None)

    def operator(self, expression: Not | Compare | Logical | Ternary) -> UtilCallInput:
        operators = self.vocabulary.operators
        if isinstance(expression, Not):
            return UtilCallInput(operation=operators["!"], arguments=(self.argument("a", expression.operand),))
        if isinstance(expression, Compare):
            return UtilCallInput(
                operation=operators[expression.operator],
                arguments=(self.argument("a", expression.left), self.argument("b", expression.right)),
            )
        if isinstance(expression, Logical):
            return UtilCallInput(
                operation=operators[expression.operator],
                arguments=(
                    ActionArgumentInput(
                        key="values", value_list=tuple(self.argument(None, operand) for operand in expression.operands)
                    ),
                ),
            )
        return UtilCallInput(
            operation=operators["?:"],
            arguments=(
                self.argument("cond", expression.cond),
                self.argument("then", expression.then),
                self.argument("otherwise", expression.otherwise),
            ),
        )

    def argument(self, key: str | None, expression: Expr) -> ActionArgumentInput:
        """An expression as a call's argument; task calls and effects are refused."""
        if isinstance(expression, Literal):
            if expression.value is None:
                return ActionArgumentInput(key=key)
            return ActionArgumentInput(key=key, value_literal=expression.value)
        if isinstance(expression, Path):
            return ActionArgumentInput(key=key, value_path=expression.dotted)
        if isinstance(expression, ListExpr):
            return ActionArgumentInput(key=key, value_list=tuple(self.argument(None, item) for item in expression.items))
        if isinstance(expression, ObjectExpr):
            return ActionArgumentInput(key=key, value_dict=tuple(self.argument(name, item) for name, item in expression.entries))
        if isinstance(expression, Into):
            raise self.fail("callback.into", expression.offset)
        if isinstance(expression, Call):
            self.check_pure(expression, expression.offset, text=False)
            return ActionArgumentInput(key=key, util_call=self.util_call(expression, effect_allowed=False))
        return ActionArgumentInput(key=key, util_call=self.operator(expression))

    def check_pure(self, expression: Expr, offset: int, *, text: bool) -> None:
        """Refuse a task call or an effect (a query's refetch or fetch_more too) where only a value may be."""
        if isinstance(expression, Call):
            callee = expression.callee
            sugar = len(callee) == 3 and callee[0] in ("queries", "subscriptions") and callee[2] in QUERY_SUGAR
            if self.agent_call_name(expression) or sugar:
                if text:
                    raise self.fail("bsx.text_callback", offset)
                raise self.fail("callback.nested", expression.offset, what=f"{expression.name}(...)")
            function = self.view.functions.get(expression.name)
            if function is not None and function.effect:
                if text:
                    raise self.fail("bsx.text_callback", offset)
                raise self.fail("callback.nested", expression.offset, what=expression.name)
            for argument in expression.arguments:
                self.check_pure(argument.value, argument.offset, text=text)
            return
        for inner in _children(expression):
            self.check_pure(inner, offset, text=text)

    @staticmethod
    def agent_call_name(call: Call) -> bool:
        callee = call.callee
        return (callee[0] == "self" and len(callee) == 2) or (callee[0] == "deps" and len(callee) == 3)


def _children(expression: Expr) -> tuple[Expr, ...]:
    if isinstance(expression, ListExpr):
        return expression.items
    if isinstance(expression, ObjectExpr):
        return tuple(value for _, value in expression.entries)
    if isinstance(expression, Not):
        return (expression.operand,)
    if isinstance(expression, Compare):
        return (expression.left, expression.right)
    if isinstance(expression, Logical):
        return expression.operands
    if isinstance(expression, Ternary):
        return (expression.cond, expression.then, expression.otherwise)
    if isinstance(expression, Call):
        return tuple(argument.value for argument in expression.arguments)
    if isinstance(expression, Into):
        return (expression.call,)
    return ()


def parse_bsx(source: str, *, view: BlokView | None = None) -> ComponentNodeInput:
    """Parse a v2 BSX source into the tree a blok stores.

    Args:
        source: The blok's source: one root element.
        view: The catalogs that say which prop text children fill; the Orkestrator's
            (base, the standard catalogs and its module) when left out.

    Raises:
        BlokError: With the code of the first problem and where it is (``line``, ``column``).
    """
    return parse_bsx_with_spans(source, view=view)[0]


def parse_bsx_with_spans(source: str, *, view: BlokView | None = None) -> tuple[ComponentNodeInput, dict[str, tuple[int, int]]]:
    """:func:`parse_bsx`, and per node id the line and column (1-based) its element starts at.

    The stored tree keeps no positions; this map is how a checker that parsed the source points
    a finding (a node id) back into it. An implicit text node is placed at its parent.

    Raises:
        BlokError: As :func:`parse_bsx`.
    """
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise BlokError("bsx.syntax", detail=f"a blok's source is at most {MAX_SOURCE_BYTES} bytes")
    locate = Locator(source)
    markup = _Markup(source, locate)
    root = markup.document()
    lowering = _Lowering(view or default_view(), locate, markup)
    tree = lowering.node(root, f"{root.name}[0]", "HTML", None)
    return tree, lowering.spans


def parse_argument(key: str | None, text: str, *, view: BlokView | None = None) -> ActionArgumentInput:
    """An expression (``args.slide.id``) as a call argument is stored: a query's variable.

    Raises:
        BlokError: If the expression does not parse, or is a task call or an effect.
    """
    locate = Locator(text)
    expression = parse_expression(text, locate=locate)
    return _Lowering(view or default_view(), locate, _Markup(text, locate)).argument(key, expression)


__all__ = ["FETCH_MORE_FUNCTION", "MAX_SOURCE_BYTES", "QUERY_SUGAR", "REFETCH_FUNCTION", "jsx_text", "parse_argument", "parse_bsx", "parse_bsx_with_spans"]
