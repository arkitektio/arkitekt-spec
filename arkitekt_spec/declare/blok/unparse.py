"""Print a component tree back as BSX: the inverse of :func:`~arkitekt_spec.declare.blok.parser.bsx`.

A blok on the server is a tree, not the text it was written as, and the parser
keeps no source. Whoever edits a stored blok -- a person in an editor, a model in
a chat -- needs it in the language they write, so this prints one.

The contract is ``bsx(to_bsx(tree)) == tree``. A tree the parser could not have
produced is refused rather than approximated, because text that re-parses into a
*different* tree changes the blok silently on the next save:

- a static value that is not a string (``True``, ``3``): BSX attributes are text,
- a static string the parser would read as an expression (``@x``, ``$x``) or
  whose surrounding whitespace it would strip,
- a ``DynamicValueInput.literal`` fallback, which has no spelling,
- a path or operation segment that is not a Python identifier (``items.0``,
  ``logic.if``): the parser reads both through :mod:`ast`.
"""

import keyword
import math
from collections.abc import Sequence

from arkitekt_spec.actions import (
    ActionArgumentInput,
    AgentProbeInput,
    ComponentNodeInput,
    ComponentPropInput,
    UtilCallInput,
)
from arkitekt_spec.declare.blok.parser import (
    _STRUCTURE_IDENTIFIER,  # pyright: ignore[reportPrivateUsage]
    BOUND_CALL_STEP,
    EVENT_ROOT,
    EVENT_SCOPE_KEY,
)
from arkitekt_spec.declare.blok.walk import FOREACH_COMPONENT, FOREACH_LET_PROP

#: An element whose attributes fit on one line is printed on one line.
LINE_WIDTH = 100


def to_bsx(node: ComponentNodeInput, indent: int = 2) -> str:
    """Print ``node`` as BSX that :func:`bsx` parses back into the same tree.

    Node ids are printed only where they differ from the structural id the
    parser would assign (``Card/CardContent[0]/Button[1]``), so a tree parsed
    from source without ids prints without them.

    Raises:
        ValueError: If the tree holds something BSX cannot express (see the
            module docstring). The message names the node and prop.
    """
    lines: list[str] = []
    _print_node(node, node.component, 0, indent, lines)
    return "\n".join(lines)


def _print_node(
    node: ComponentNodeInput, structural_id: str, depth: int, indent: int, lines: list[str]
) -> None:
    pad = " " * (indent * depth)
    attributes: list[str] = []
    if node.id != structural_id:
        attributes.append(f"id={_quote(node.id)}")
    for prop in node.props or ():
        if prop.key == "id":
            raise ValueError(
                f"<{node.component}> ({node.id}) has a prop named 'id', which BSX reads as the node id."
            )
        attributes.append(f"{prop.key}={_quote(_prop_source(node, prop))}")

    one_line = f"{pad}<{node.component}{''.join(' ' + a for a in attributes)}"
    if len(one_line) <= LINE_WIDTH:
        lines.append(f"{one_line} />" if not node.children else f"{one_line}>")
    else:
        # One attribute per line, the bracket back at the element's indent.
        inner = " " * (indent * (depth + 1))
        lines.append(f"{pad}<{node.component}")
        lines.extend(f"{inner}{attribute}" for attribute in attributes)
        lines.append(f"{pad}/>" if not node.children else f"{pad}>")
    if not node.children:
        return

    seen: dict[str, int] = {}
    for child in node.children:
        index = seen.get(child.component, 0)
        seen[child.component] = index + 1
        _print_node(child, f"{node.id}/{child.component}[{index}]", depth + 1, indent, lines)
    lines.append(f"{pad}</{node.component}>")


def _prop_source(node: ComponentNodeInput, prop: ComponentPropInput) -> str:
    where = f"<{node.component}> ({node.id}) prop '{prop.key}'"

    if node.component.lower() == FOREACH_COMPONENT and prop.key == FOREACH_LET_PROP:
        if prop.declares_value is None or prop.static_value != prop.declares_value:
            raise ValueError(f"{where}: a loop variable is declared as both static and declared value.")
        return f"#{prop.declares_value}"

    if prop.agent_call is not None:
        call = f"@{_agent_call(prop.agent_call, where)}"
        if prop.declares_value is not None:
            call += f".{BOUND_CALL_STEP}({_identifier(prop.declares_value, where)})"
        return call
    if prop.declares_value is not None:
        raise ValueError(f"{where}: only a call to an agent or a foreach 'let' can declare a value.")

    if prop.util_call is not None:
        return f"@{_util_call(prop.util_call, where)}"

    if prop.dynamic_value is not None:
        if prop.dynamic_value.literal is not None:
            raise ValueError(f"{where}: a dynamic value's fallback literal has no BSX spelling.")
        if prop.dynamic_value.path is None:
            raise ValueError(f"{where}: a dynamic value without a path.")
        return f"@{_dotted(prop.dynamic_value.path, where)}"

    value = prop.static_value
    if value is None:
        raise ValueError(f"{where}: no value.")
    if not isinstance(value, str):
        raise ValueError(
            f"{where}: the static value {value!r} is a {type(value).__name__}; BSX attributes are text."
        )
    if value != value.strip():
        raise ValueError(f"{where}: leading or trailing whitespace in {value!r} is stripped by the parser.")
    if value.startswith("$") or (value.startswith("@") and not _STRUCTURE_IDENTIFIER.fullmatch(value)):
        raise ValueError(f"{where}: the static value {value!r} would be read as an expression.")
    return value


def _agent_call(call: AgentProbeInput, where: str) -> str:
    callee = f"{_identifier(call.dependency, where)}.{_dotted(call.operation, where)}"
    if call.dependency == "utils":
        raise ValueError(f"{where}: an agent call on a dependency named 'utils' reads as a utils call.")
    return f"{callee}({_arguments(call.arguments, where)})"


def _util_call(call: UtilCallInput, where: str) -> str:
    return f"utils.{_dotted(call.operation, where)}({_arguments(call.arguments, where)})"


def _arguments(arguments: Sequence[ActionArgumentInput] | None, where: str) -> str:
    """Positional arguments (keyed ``"0"``, ``"1"``, ...) first and in order, then named ones."""
    rendered: list[str] = []
    expected = 0
    named = False
    for argument in arguments or ():
        if argument.key is None:
            raise ValueError(f"{where}: a call argument without a key.")
        if argument.key.isdigit():
            if named or int(argument.key) != expected:
                raise ValueError(
                    f"{where}: positional argument '{argument.key}' is out of order; "
                    "positional arguments are keyed 0, 1, ... before any named one."
                )
            expected += 1
            rendered.append(_value(argument, where))
        else:
            named = True
            rendered.append(f"{_identifier(argument.key, where)}={_value(argument, where)}")
    return ", ".join(rendered)


def _value(argument: ActionArgumentInput, where: str) -> str:
    if argument.agent_call is not None:
        return _agent_call(argument.agent_call, where)
    if argument.util_call is not None:
        return _util_call(argument.util_call, where)
    if argument.value_path is not None:
        return _argument_path(argument.value_path, where)
    if argument.value_list is not None:
        return f"[{', '.join(_value(item, where) for item in argument.value_list)}]"
    if argument.value_dict is not None:
        entries = []
        for entry in argument.value_dict:
            if not entry.key:
                raise ValueError(f"{where}: a dict entry without a key.")
            entries.append(f"{entry.key!r}: {_value(entry, where)}")
        return f"{{{', '.join(entries)}}}"
    return _literal(argument.value_literal, where)


def _literal(value: object, where: str) -> str:
    if value is None:
        raise ValueError(f"{where}: an argument without a value; the parser refuses None.")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{where}: {value!r} has no literal spelling.")
    if isinstance(value, (bool, int, float, str)):
        return repr(value)
    raise ValueError(f"{where}: the literal {value!r} is not a string, number or boolean.")


def _argument_path(path: str, where: str) -> str:
    """An argument path; ``$event`` is spelled ``event``, as the parser maps it."""
    root, _, rest = path.partition(".")
    if root == EVENT_SCOPE_KEY:
        return f"{EVENT_ROOT}.{_dotted(rest, where)}" if rest else EVENT_ROOT
    if root == EVENT_ROOT:
        raise ValueError(f"{where}: the argument path {path!r} would be read as the triggering event.")
    return _dotted(path, where)


def _dotted(dotted: str, where: str) -> str:
    return ".".join(_identifier(part, where) for part in dotted.split("."))


def _identifier(name: str, where: str) -> str:
    if not name.isidentifier() or keyword.iskeyword(name):
        raise ValueError(f"{where}: {name!r} is not a name BSX can spell.")
    return name


def _quote(value: str) -> str:
    """An XML attribute, single-quoted when that spares escaping double quotes (JSON values).

    Newlines and tabs are escaped: the parser would turn them into spaces.
    """
    quote = "'" if '"' in value and "'" not in value else '"'
    escaped = (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace(quote, "&quot;" if quote == '"' else "&apos;")
        .replace("\n", "&#10;")
        .replace("\r", "&#13;")
        .replace("\t", "&#9;")
    )
    return f"{quote}{escaped}{quote}"
