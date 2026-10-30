import ast
import functools
import io
import keyword
import re
import tokenize

from arkitekt_spec.actions import (
    ActionArgumentInput,
    ComponentNodeInput,
    UtilCallInput,
)
from arkitekt_spec.blok.bsx import parse_bsx
from arkitekt_spec.blok.vocabulary import load_vocabulary
from arkitekt_spec.declare.traits.calls import resolve_base_arguments
from arkitekt_spec.rules import check_fits

# ``@dep.op(...).into(name)`` binds the call's task to ``name``.
BOUND_CALL_STEP = "into"

# ``@mikro/arraydataset``: what a structure port carries as its identifier.
_STRUCTURE_IDENTIFIER = re.compile(r"@[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_DESCRIPTOR_KEY = re.compile(r"(?<![\w\"'])@[A-Za-z0-9_-]+/[A-Za-z0-9_-]+")


EVENT_ROOT = "event"
"""How a handler's arguments name the value that triggered it."""

EVENT_SCOPE_KEY = "$event"
"""The path root the renderer publishes that value under."""


class PortCallParser:
    """Parses a pure port-call expression such as ``value > 3`` or ``gt(value, other.min)``.

    Port validators and effects carry a :class:`UtilCallInput` that the UI evaluates
    against its catalog. The expression is a Python expression subset:

    - a call whose callee is the operation (``gt``, ``math.multiply``); a leading
      ``utils.`` or ``@`` is tolerated and stripped,
    - operator sugar desugared onto base-catalog operations: comparisons
      (``> >= < <= == !=`` -> ``gt gte lt lte eq ne``, chains fold with ``and``),
      ``and``/``or``/``not``, ``in``/``not in``, ``is None``/``is not None``
      (``is_null``/``is_set``), ``x if c else y`` (``if``) and arithmetic
      (``+ - * / %`` -> ``add sub mul div mod``, unary minus -> ``neg``; ``//`` and ``**``
      are not supported),
    - bare names, attribute chains and subscripts are value paths (``value`` is the
      port's own value, ``other.min`` / ``other["min"]`` the field ``min`` inside the
      value of port ``other``), emitted JSON-pointer style (``other/min``),
    - constants, keyword arguments, nested calls, lists and dicts work as in blok
      props. Positional arguments of base operations are named after the operation's
      parameters; for other operations they are keyed by index (``"0"``, ``"1"``, ...),
      which the UI maps onto parameters in order.

    Port calls must be pure: ``actions.*`` callees are rejected and every other callee is
    a catalog operation.
    """

    KEYWORD_PREFIX = "kw__"
    """Prefix used to smuggle Python keywords (``if``, ``and``, ``not``) through ``ast``."""

    _CALLEE_PRECEDERS = frozenset({"(", "[", "{", ",", "=", ":"})

    _ARITHMETIC: dict[type[ast.operator], str] = {
        ast.Add: "add",
        ast.Sub: "sub",
        ast.Mult: "mul",
        ast.Div: "div",
        ast.Mod: "mod",
    }

    _COMPARISONS: dict[type[ast.cmpop], str] = {
        ast.Gt: "gt",
        ast.GtE: "gte",
        ast.Lt: "lt",
        ast.LtE: "lte",
        ast.Eq: "eq",
        ast.NotEq: "ne",
    }

    @classmethod
    def _escape_keyword_names(cls, source: str) -> str:
        """Rename keyword tokens used as callees or attributes so ``ast`` accepts them.

        Catalog operations may be named like Python keywords (``if``, ``and``,
        ``not``). A keyword directly followed by ``(`` or preceded by ``.`` is
        prefixed with :attr:`KEYWORD_PREFIX`; :meth:`_unescape_name` restores it.
        """
        try:
            tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
        except tokenize.TokenError as e:
            raise ValueError(f"Failed to parse port call '{source}': {e}") from e

        skipped = {tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT}
        significant = [tok for tok in tokens if tok.type not in skipped]
        renamed: list[tuple[int, str]] = []
        for index, tok in enumerate(significant):
            string = tok.string
            if tok.type == tokenize.NAME and keyword.iskeyword(string):
                nxt = significant[index + 1] if index + 1 < len(significant) else None
                prev = significant[index - 1] if index > 0 else None
                follows_dot = prev is not None and prev.type == tokenize.OP and prev.string == "."
                precedes_call = nxt is not None and nxt.type == tokenize.OP and nxt.string == "("
                # Only a keyword in *callee* position is an operation name (``if(`` at the start,
                # after ``(``, ``,``, ``=``, ...). ``x and (y)`` or ``not (x)`` are operators and
                # must reach Python's grammar, where the desugarer maps them onto the same names.
                callee_position = prev is None or (
                    prev.type == tokenize.OP and prev.string in cls._CALLEE_PRECEDERS
                )
                if follows_dot or (precedes_call and callee_position):
                    string = cls.KEYWORD_PREFIX + string
            renamed.append((tok.type, string))
        return tokenize.untokenize(renamed)

    @classmethod
    def _unescape_name(cls, path: str, separator: str) -> str:
        prefix = cls.KEYWORD_PREFIX
        return separator.join(
            part.removeprefix(prefix)
            for part in path.split(separator)
        )

    @classmethod
    def parse_expression(cls, expression: str) -> UtilCallInput:
        source = expression.strip()
        if source.startswith("@"):
            source = source[1:].lstrip()
        try:
            tree = ast.parse(cls._escape_keyword_names(source), mode="eval")
        except SyntaxError as e:
            raise ValueError(f"Failed to parse port call '{expression}': {e}") from e

        if not cls._is_call_like(tree.body):
            raise ValueError(
                "A port call must be a call or boolean expression like 'gt(value, 3)' or "
                f"'value > 3'. Got: '{expression}'"
            )
        return cls._desugar(tree.body)

    @classmethod
    def _is_call_like(cls, node: ast.AST) -> bool:
        return isinstance(node, (ast.Call, ast.Compare, ast.BoolOp, ast.IfExp, ast.BinOp)) or (
            isinstance(node, ast.UnaryOp) and not cls._is_signed_constant(node)
        )

    @staticmethod
    def _is_signed_constant(node: ast.UnaryOp) -> bool:
        """``-1`` / ``+2.5``: a literal, handled by the base argument parser."""
        return (
            isinstance(node.op, (ast.USub, ast.UAdd))
            and isinstance(node.operand, ast.Constant)
            and isinstance(node.operand.value, (int, float))
            and not isinstance(node.operand.value, bool)
        )

    @classmethod
    def _desugar(cls, node: ast.AST) -> UtilCallInput:
        """Turn a call or operator expression into a base-catalog call tree."""
        if isinstance(node, ast.Call):
            return cls._parse_ast_call(node)

        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            parts = [
                cls._desugar_comparison(op, left, right)
                for op, left, right in zip(node.ops, operands, operands[1:])
            ]
            return functools.reduce(lambda acc, part: cls._base_call("and", a=acc, b=part), parts)

        if isinstance(node, ast.BoolOp):
            name = "and" if isinstance(node.op, ast.And) else "or"
            return functools.reduce(
                lambda acc, value: cls._base_call(name, a=acc, b=value), node.values
            )

        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                return cls._base_call("not", a=node.operand)
            if isinstance(node.op, ast.USub):
                return cls._base_call("neg", a=node.operand)
            if isinstance(node.op, ast.UAdd):
                if cls._is_call_like(node.operand):
                    return cls._desugar(node.operand)
                raise ValueError("Unary plus on a value is not an expression; write the value itself")
            raise ValueError("Bitwise operators are not supported in port calls")

        if isinstance(node, ast.IfExp):
            return cls._base_call("if", condition=node.test, then=node.body, otherwise=node.orelse)

        if isinstance(node, ast.BinOp):
            name = cls._ARITHMETIC.get(type(node.op))
            if name is None:
                raise ValueError(
                    f"Operator {type(node.op).__name__} is not supported in port calls "
                    f"(the catalog has {', '.join(cls._ARITHMETIC.values())})"
                )
            return cls._base_call(name, a=node.left, b=node.right)
        raise ValueError(f"Unsupported expression in port call: {type(node).__name__}")

    @classmethod
    def _desugar_comparison(cls, op: ast.cmpop, left: ast.AST, right: ast.AST) -> UtilCallInput:
        if isinstance(op, ast.In):
            return cls._base_call("in", value=left, options=right)
        if isinstance(op, ast.NotIn):
            return cls._base_call("not", a=cls._base_call("in", value=left, options=right))
        if isinstance(op, (ast.Is, ast.IsNot)):
            if not (isinstance(right, ast.Constant) and right.value is None):
                raise ValueError("'is' is only supported as 'is None' / 'is not None'")
            return cls._base_call("is_null" if isinstance(op, ast.Is) else "is_set", a=left)
        name = cls._COMPARISONS.get(type(op))
        if name is None:
            raise ValueError(f"Unsupported comparison in port call: {type(op).__name__}")
        return cls._base_call(name, a=left, b=right)

    @classmethod
    def _base_call(cls, operation: str, **operands: "ast.AST | UtilCallInput") -> UtilCallInput:
        """A base-catalog call with named arguments, from AST nodes or already-built calls."""
        arguments = [
            ActionArgumentInput(key=key, util_call=operand)
            if isinstance(operand, UtilCallInput)
            else cls._parse_ast_argument_value(operand, key=key)
            for key, operand in operands.items()
        ]
        arguments = resolve_base_arguments(operation, arguments, f"{operation}(...)")
        return UtilCallInput(operation=operation, arguments=tuple(arguments))

    @classmethod
    def _parse_ast_argument_value(  # type: ignore[override]
        cls, node: ast.AST, key: str | None = None
    ) -> ActionArgumentInput:
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd) and not cls._is_signed_constant(node):
            return cls._parse_ast_argument_value(node.operand, key)  # ``+x`` is ``x``
        if cls._is_call_like(node) and not isinstance(node, ast.Call):
            return ActionArgumentInput(key=key, util_call=cls._desugar(node))
        if isinstance(node, ast.Subscript):
            return ActionArgumentInput(key=key, value_path=cls._subscript_path(node))
        return cls._parse_plain_argument(node, key)

    @classmethod
    def _subscript_path(cls, node: ast.AST) -> str:
        """``other["k"]`` / ``items[0]`` / ``other.list[1].x`` as a JSON-pointer path."""
        segments: list[str] = []
        while isinstance(node, (ast.Subscript, ast.Attribute)):
            if isinstance(node, ast.Attribute):
                segments.insert(0, cls._pointer_segment(cls._unescape_name(node.attr, ".")))
                node = node.value
                continue
            index = node.slice
            if isinstance(index, ast.Constant) and isinstance(index.value, str) and index.value:
                segments.insert(0, cls._pointer_segment(index.value))
            elif (
                isinstance(index, ast.Constant)
                and isinstance(index.value, int)
                and not isinstance(index.value, bool)
                and index.value >= 0
            ):
                segments.insert(0, str(index.value))
            else:
                raise ValueError(
                    "Subscripts in port calls must be a non-empty string key or a non-negative "
                    "integer index"
                )
            node = node.value
        if not isinstance(node, ast.Name):
            raise ValueError(
                f"Unsupported AST node type for a value path: {type(node).__name__} "
                "(paths start with a port name or 'value')"
            )
        root = cls._unescape_name(node.id, ".")
        return "/".join([root, *segments])

    @staticmethod
    def _pointer_segment(segment: str) -> str:
        """RFC 6901 escaping of a single JSON-pointer segment."""
        return segment.replace("~", "~0").replace("/", "~1")

    @classmethod
    def _parse_ast_call(cls, node: ast.Call) -> UtilCallInput:  # type: ignore[override]
        full_path = cls._extract_path(node.func)
        path_parts = full_path.split(".")

        if path_parts[0] == "actions":
            raise ValueError(
                f"Port calls must be pure: agent calls like '{full_path}(...)' are not allowed"
            )
        if path_parts[0] == "utils":
            path_parts = path_parts[1:]
            if not path_parts:
                raise ValueError(f"Invalid utils namespace: '{full_path}'.")

        operation = cls._unescape_name(".".join(path_parts), ".")
        arguments = resolve_base_arguments(
            operation, cls._parse_call_arguments(node), f"{operation}(...)"
        )

        return UtilCallInput(
            operation=operation,
            arguments=tuple(arguments) if arguments else None,
        )

    @classmethod
    def _argument_path(cls, node: ast.AST) -> str:
        return cls._subscript_path(node)

    @classmethod
    def _parse_call_arguments(cls, node: ast.Call) -> list[ActionArgumentInput]:
        """Parse a call's arguments into keyed entries.

        Call arguments are a map on the wire: positional arguments are keyed by
        their index (``"0"``, ``"1"``, ...; the UI maps them onto the operation's
        parameters in order) and keyword arguments by name.
        """
        arguments = [
            cls._parse_ast_argument_value(arg_node, key=str(index))
            for index, arg_node in enumerate(node.args)
        ]
        for kw in node.keywords:
            if kw.arg is None:
                raise ValueError("Call arguments cannot be unpacked with '**'")
            arguments.append(cls._parse_ast_argument_value(kw.value, key=kw.arg))
        return arguments

    @classmethod
    def _parse_plain_argument(
        cls, node: ast.AST, key: str | None = None
    ) -> ActionArgumentInput:
        """Recursively parses AST nodes into ActionArgumentInput models."""

        # Literal Values (Strings, Ints, Floats, Bools)
        if isinstance(node, ast.Constant):
            if node.value is None:
                raise ValueError(
                    f"Argument '{key or 'item'}' cannot be None: a missing literal is "
                    "indistinguishable from no value. Omit the argument instead."
                )
            return ActionArgumentInput(key=key, value_literal=node.value)  # pyright: ignore[reportArgumentType] -- ast constants

        # Signed numeric literals (-1, +2.5)
        elif (
            isinstance(node, ast.UnaryOp)
            and isinstance(node.op, (ast.USub, ast.UAdd))
            and isinstance(node.operand, ast.Constant)
            and isinstance(node.operand.value, (int, float))
            and not isinstance(node.operand.value, bool)
        ):
            value = node.operand.value
            if isinstance(node.op, ast.USub):
                value = -value
            return ActionArgumentInput(key=key, value_literal=value)

        # State Paths
        elif isinstance(node, (ast.Name, ast.Attribute)):
            path = cls._argument_path(node)
            return ActionArgumentInput(key=key, value_path=path)

        # Nested calls
        elif isinstance(node, ast.Call):
            return ActionArgumentInput(key=key, util_call=cls._parse_ast_call(node))

        # Lists and Tuples
        elif isinstance(node, (ast.List, ast.Tuple)):
            parsed_elements = [
                cls._parse_ast_argument_value(elt, key=None) for elt in node.elts
            ]
            return ActionArgumentInput(key=key, value_list=tuple(parsed_elements))

        # Dictionaries -> GraphQL Key/Value Arrays
        elif isinstance(node, ast.Dict):
            parsed_dict_list = []
            for k_node, v_node in zip(node.keys, node.values):
                if (
                    not isinstance(k_node, ast.Constant)
                    or not isinstance(k_node.value, str)
                    or not k_node.value
                ):
                    raise ValueError("Dictionary keys must be non-empty literal strings.")
                parsed_dict_list.append(
                    cls._parse_ast_argument_value(v_node, key=k_node.value)
                )
            return ActionArgumentInput(key=key, value_dict=tuple(parsed_dict_list))

        else:
            raise ValueError(
                f"Unsupported AST node type for argument '{key or 'item'}': {type(node).__name__}"
            )

    @classmethod
    def _extract_path(cls, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return f"{cls._extract_path(node.value)}.{node.attr}"
        raise ValueError(f"Cannot extract path from node type: {type(node).__name__}")


def parse_util_call(expression: str) -> UtilCallInput:
    """Parse a pure port-call expression into a :class:`UtilCallInput`.

    Used for port validators and effects. ``value`` refers to the port's own value,
    other names refer to sibling ports (``other`` or ``other.child``); operator sugar,
    nested calls, literals, keyword arguments, lists and dicts are supported (see
    :class:`PortCallParser`).

    Examples:
        ``parse_util_call("value > 3")`` and ``parse_util_call("gt(value, 3)")`` both
        give ``gt`` with ``a`` bound to ``value_path="value"`` and ``b`` to
        ``value_literal=3``; ``parse_util_call("gt(value, other.min)")`` references
        ``other/min``.

    Raises:
        ValueError: If the expression is not a call, does not parse, or contains an
            agent call.
    """
    return PortCallParser.parse_expression(expression)


class EstimatedRuntimeParser(PortCallParser):
    """The port-call grammar with ``**`` too: a formula names the vocabulary, which has ``math.pow``."""

    _ARITHMETIC = {**PortCallParser._ARITHMETIC, ast.Pow: "pow"}


_VOCABULARY_NAMES = {
    "add": "math.add",
    "sub": "math.sub",
    "mul": "math.mul",
    "div": "math.div",
    "mod": "math.mod",
    "pow": "math.pow",
    "neg": "math.neg",
    "eq": "cmp.eq",
    "ne": "cmp.ne",
    "gt": "cmp.gt",
    "gte": "cmp.gte",
    "lt": "cmp.lt",
    "lte": "cmp.lte",
    "not": "logic.negate",
    "if": "logic.choose",
    "is_null": "val.is_null",
    "is_set": "val.is_set",
}
"""What the parser's sugar is called in the blok vocabulary, the only functions the renderer has."""

_VOCABULARY_KEYS = {("if", "condition"): "cond", ("is_null", "a"): "value", ("is_set", "a"): "value"}
"""The arguments the vocabulary names differently from the base catalog."""

_VOCABULARY_FOLDS = {"and": "logic.all", "or": "logic.any", "coalesce": "logic.coalesce"}
"""What the base catalog takes as two arguments and the vocabulary as one list."""


def _in_vocabulary(call: UtilCallInput, owner: str) -> UtilCallInput:
    """``call``, its operations and argument keys renamed to the blok vocabulary's."""
    if call.operation in _VOCABULARY_FOLDS:
        operands = tuple(
            argument.model_copy(update={"key": None}) for argument in _vocabulary_arguments(call, owner)
        )
        return UtilCallInput(
            operation=_VOCABULARY_FOLDS[call.operation],
            arguments=(ActionArgumentInput(key="values", value_list=operands),),
        )

    operation = _VOCABULARY_NAMES.get(call.operation, call.operation)
    function = load_vocabulary().function(operation)
    if function is None or function.effect:
        raise ValueError(f"{owner}: {operation} is not a pure function of the blok language")

    arguments = []
    for index, argument in enumerate(_vocabulary_arguments(call, owner)):
        key = argument.key or str(index)
        key = _VOCABULARY_KEYS.get((call.operation, key), key)
        if key.isdigit():
            if int(key) >= len(function.keys):
                raise ValueError(f"{owner}: {operation} takes {', '.join(function.keys)}")
            key = function.keys[int(key)]
        if key not in function.keys:
            raise ValueError(f"{owner}: {operation} has no argument {key} ({', '.join(function.keys)})")
        arguments.append(argument.model_copy(update={"key": key}))

    missing = set(function.required_keys) - {argument.key for argument in arguments}
    if missing:
        raise ValueError(f"{owner}: {operation} needs {', '.join(sorted(missing))}")
    return UtilCallInput(operation=operation, arguments=tuple(arguments))


def _vocabulary_arguments(call: UtilCallInput, owner: str) -> list[ActionArgumentInput]:
    """The arguments of ``call`` with every call nested in them renamed."""

    def renamed(argument: ActionArgumentInput) -> ActionArgumentInput:
        update: dict[str, object] = {}
        if argument.util_call is not None:
            update["util_call"] = _in_vocabulary(argument.util_call, owner)
        if argument.value_list is not None:
            update["value_list"] = tuple(renamed(item) for item in argument.value_list)
        if argument.value_dict is not None:
            update["value_dict"] = tuple(renamed(item) for item in argument.value_dict)
        return argument.model_copy(update=update)

    return [renamed(argument) for argument in call.arguments or ()]


def parse_estimated_runtime(expression: str) -> UtilCallInput:
    """Parse a runtime formula, in seconds, into a call of the blok vocabulary.

    The formula is parsed, never run: Python's grammar only reads it, and what comes out is
    a tree of pure functions. Port calls name the base catalog (``mul``); this names what the
    renderer implements (``math.mul``). What each formula may then use is the rules' to say:
    :func:`arkitekt_spec.rules.check_estimated_runtime` for ``estimated_runtime``, which the
    server computes, and :func:`arkitekt_spec.rules.check_estimated_runtime_ui` for
    ``estimated_runtime_ui``, which only the frontend does.

    Example:
        ``parse_estimated_runtime('args.image["@mikro/size"] / agent.cpu_count / 5e7')``

    Raises:
        ValueError: If the formula does not parse, or calls anything but a pure function.
    """
    return _in_vocabulary(EstimatedRuntimeParser.parse_expression(expression), f"Estimated runtime '{expression}'")


def parse_fits(expression: str) -> UtilCallInput:
    """Parse what a deployment can handle into a call of the blok vocabulary.

    The formula is parsed, never run. A bare ``@pkg/key`` is a descriptor of the argument it
    annotates, ``agent.memory_mib`` the agent's hardware. The server compiles the result to a
    database predicate, so only what that predicate can express passes
    (:func:`arkitekt_spec.rules.check_fits`).

    Example:
        ``parse_fits("@mikro/size * @mikro/dsize < agent.memory_mib * 1048576")``

    Raises:
        ValueError: If the formula does not parse, or uses anything a predicate cannot express.
    """
    source = _DESCRIPTOR_KEY.sub(lambda key: f'value["{key.group()}"]', expression)
    owner = f"Fits '{expression}'"
    call = _in_vocabulary(EstimatedRuntimeParser.parse_expression(source), owner)
    check_fits(call, owner)
    return call


def coerce_util_call(call: "str | UtilCallInput") -> UtilCallInput:
    """Return ``call`` as a :class:`UtilCallInput`, parsing it if it is an expression."""
    if isinstance(call, UtilCallInput):
        return call
    return parse_util_call(call)



def bsx(source: str) -> ComponentNodeInput:
    """Parse a v2 BSX source into the tree a blok stores (see :func:`arkitekt_spec.blok.bsx.parse_bsx`).

    Raises:
        BlokError: A ``ValueError`` with the diagnostic code of what is wrong and where.
    """
    return parse_bsx(source)


def normalize_expression(expression: str) -> str:
    """The ``source`` form of an authored expression: stripped, without the blok ``@`` prefix."""
    return expression.strip().removeprefix("@").strip()
