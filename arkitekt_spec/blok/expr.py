"""The blok expression language: a small JavaScript subset, parsed by hand.

The same grammar is implemented in Rust (takt) and TypeScript (orkestrator) and held to one
corpus, so it is written here without Python's :mod:`ast`::

    callback   := expression ('into' name)?
    expression := ternary
    ternary    := coalesce ('?' expression ':' expression)?
    coalesce   := or ('??' or)*
    or         := and ('||' and)*
    and        := equality ('&&' equality)*
    equality   := relation (('==' | '!=') relation)?
    relation   := unary (('>' | '>=' | '<' | '<=') unary)?
    unary      := '!' unary | '-' number | primary
    primary    := literal | path | call | list | object | '(' expression ')'
    path       := name ('.' name)*
    call       := path '(' (argument (',' argument)* ','?)? ')'
    argument   := name ':' expression | expression
    list       := '[' (expression (',' expression)* ','?)? ']'
    object     := '{' ((name | string) ':' expression (',' ...)* ','?)? '}'
    literal    := number | string | 'true' | 'false' | 'null'

Strings are quoted with ``"`` or ``'`` and know the escapes ``\\\\ \\" \\' \\n \\t \\r \\uXXXX``.
Numbers are JSON numbers. Comparisons do not chain. ``a ?? b ?? c`` is one coalesce over its
operands, as ``&&`` and ``||`` are one call over theirs; a parenthesized group stays one operand. Everything else JavaScript has is refused
with a code of its own (arithmetic, subscripts, lambdas, assignments, sequences, ``await``).
"""

from dataclasses import dataclass

from arkitekt_spec.blok.vocabulary import BlokError

Scalar = None | bool | int | float | str


@dataclass(frozen=True)
class Literal:
    """A string, number, boolean or ``null``."""

    value: Scalar
    offset: int


@dataclass(frozen=True)
class ListExpr:
    """``[a, b]``."""

    items: tuple["Expr", ...]
    offset: int


@dataclass(frozen=True)
class ObjectExpr:
    """``{key: value}``."""

    entries: tuple[tuple[str, "Expr"], ...]
    offset: int


@dataclass(frozen=True)
class Path:
    """``deps.robot.arm.joints``: a root and the segments below it."""

    segments: tuple[str, ...]
    offset: int

    @property
    def dotted(self) -> str:
        """The path as it is stored."""
        return ".".join(self.segments)


@dataclass(frozen=True)
class Argument:
    """A call's argument: ``name: value`` or positional (``name`` None)."""

    name: str | None
    value: "Expr"
    offset: int


@dataclass(frozen=True)
class Call:
    """``str.upper(x)``, ``self.move(to: 1)``, ``queries.positions.refetch()``."""

    callee: tuple[str, ...]
    arguments: tuple[Argument, ...]
    offset: int

    @property
    def name(self) -> str:
        """The callee as written."""
        return ".".join(self.callee)


@dataclass(frozen=True)
class Not:
    """``!a``."""

    operand: "Expr"
    offset: int


@dataclass(frozen=True)
class Compare:
    """``a == b``, ``a < b``, ..."""

    operator: str
    left: "Expr"
    right: "Expr"
    offset: int


@dataclass(frozen=True)
class Logical:
    """``a && b && c``, ``a || b`` or ``a ?? b``: one operator over every operand."""

    operator: str
    operands: tuple["Expr", ...]
    offset: int


@dataclass(frozen=True)
class Ternary:
    """``cond ? then : otherwise``."""

    cond: "Expr"
    then: "Expr"
    otherwise: "Expr"
    offset: int


@dataclass(frozen=True)
class Into:
    """``<call> into <name>``: a task call whose record is named."""

    call: Call
    name: str
    offset: int


Expr = Literal | ListExpr | ObjectExpr | Path | Call | Not | Compare | Logical | Ternary | Into


# --------------------------------------------------------------------------- tokens


@dataclass(frozen=True)
class Token:
    """A token: its kind (``name``, ``number``, ``string``, ``op``, ``end``), value and offset."""

    kind: str
    value: str
    offset: int
    literal: Scalar = None


_OPERATORS = (
    "===", "!==", "=>", "==", "!=", ">=", "<=", "&&", "||", "??", "?.", "**",
    "(", ")", "[", "]", "{", "}", ",", ":", ".", "?", "!", ">", "<", "=", ";",
    "+", "-", "*", "/", "%", "&", "|", "^", "~", "`",
)
_ESCAPES = {"\\": "\\", '"': '"', "'": "'", "n": "\n", "t": "\t", "r": "\r", "/": "/", "b": "\b", "f": "\f"}
_ARITHMETIC = {"+": "add", "-": "sub", "*": "mul", "/": "div", "%": "mod", "**": "pow"}


def _is_name_start(char: str) -> bool:
    return char.isascii() and (char.isalpha() or char == "_")


def _is_name_part(char: str) -> bool:
    return char.isascii() and (char.isalnum() or char == "_")


class _Lexer:
    def __init__(self, text: str, base: int, locate: "Locator") -> None:
        self.text = text
        self.base = base
        self.locate = locate

    def fail(self, code: str, offset: int, **fields: object) -> BlokError:
        """Refuse at ``offset``, an offset into the expression's text."""
        line, column = self.locate(self.base + offset)
        return BlokError(code, line=line, column=column, **fields)

    def tokens(self) -> list[Token]:
        """The tokens, each at its offset into the whole source."""
        return [Token(t.kind, t.value, t.offset + self.base, t.literal) for t in self._tokens()]

    def _tokens(self) -> list[Token]:
        text = self.text
        out: list[Token] = []
        index = 0
        while index < len(text):
            char = text[index]
            if char in " \t\r\n":
                index += 1
                continue
            if _is_name_start(char):
                end = index + 1
                while end < len(text) and _is_name_part(text[end]):
                    end += 1
                out.append(Token("name", text[index:end], index))
                index = end
                continue
            if char.isdigit():
                end, value = self._number(index)
                out.append(Token("number", text[index:end], index, value))
                index = end
                continue
            if char in "\"'":
                end, value = self._string(index)
                out.append(Token("string", text[index:end], index, value))
                index = end
                continue
            operator = next((op for op in _OPERATORS if text.startswith(op, index)), None)
            if operator is None:
                raise self.fail("expr.syntax", index, detail=f"unexpected {char!r}")
            if operator == "`":
                raise self.fail("expr.syntax", index, detail="template literals are not supported; use text children or str.template")
            out.append(Token("op", operator, index))
            index += len(operator)
        out.append(Token("end", "", len(text)))
        return out

    def _number(self, start: int) -> tuple[int, int | float]:
        text = self.text
        index = start
        while index < len(text) and text[index].isdigit():
            index += 1
        if text[start] == "0" and index - start > 1:
            raise self.fail("expr.syntax", start, detail="a number has no leading zeros")
        is_float = False
        if index < len(text) and text[index] == "." and index + 1 < len(text) and text[index + 1].isdigit():
            is_float = True
            index += 1
            while index < len(text) and text[index].isdigit():
                index += 1
        if index < len(text) and text[index] in "eE":
            exponent = index + 1
            if exponent < len(text) and text[exponent] in "+-":
                exponent += 1
            if exponent < len(text) and text[exponent].isdigit():
                is_float = True
                index = exponent
                while index < len(text) and text[index].isdigit():
                    index += 1
        if index < len(text) and _is_name_part(text[index]):
            raise self.fail("expr.syntax", start, detail=f"{text[start:index + 1]!r} is not a number")
        written = text[start:index]
        return index, float(written) if is_float else int(written)

    def _string(self, start: int) -> tuple[int, str]:
        text = self.text
        quote = text[start]
        index = start + 1
        parts: list[str] = []
        while True:
            if index >= len(text):
                raise self.fail("expr.syntax", start, detail="a string is never closed")
            char = text[index]
            if char == quote:
                return index + 1, "".join(parts)
            if char == "\n":
                raise self.fail("expr.syntax", start, detail="a string is never closed on its line")
            if char == "\\":
                escape = text[index + 1] if index + 1 < len(text) else ""
                if escape == "u":
                    digits = text[index + 2 : index + 6]
                    if len(digits) != 4 or any(c not in "0123456789abcdefABCDEF" for c in digits):
                        raise self.fail("expr.syntax", index, detail="\\u takes four hex digits")
                    parts.append(chr(int(digits, 16)))
                    index += 6
                    continue
                if escape not in _ESCAPES:
                    raise self.fail("expr.syntax", index, detail=f"unknown escape \\{escape}")
                parts.append(_ESCAPES[escape])
                index += 2
                continue
            parts.append(char)
            index += 1


class Locator:
    """Turns an offset into the whole source into a (line, column), both 1-based."""

    def __init__(self, source: str) -> None:
        """Index where each line of ``source`` starts."""
        self.source = source
        self.starts = [0]
        for index, char in enumerate(source):
            if char == "\n":
                self.starts.append(index + 1)

    def __call__(self, offset: int) -> tuple[int, int]:
        """The 1-based line and column of ``offset``."""
        low, high = 0, len(self.starts) - 1
        while low < high:
            middle = (low + high + 1) // 2
            if self.starts[middle] <= offset:
                low = middle
            else:
                high = middle - 1
        return low + 1, offset - self.starts[low] + 1


# --------------------------------------------------------------------------- parser


class _Parser:
    def __init__(self, text: str, base: int, locate: Locator) -> None:
        self.lexer = _Lexer(text, base, locate)
        self.tokens = self.lexer.tokens()
        self.index = 0

    @property
    def token(self) -> Token:
        return self.tokens[self.index]

    def peek(self, ahead: int = 1) -> Token:
        return self.tokens[min(self.index + ahead, len(self.tokens) - 1)]

    def fail(self, code: str, offset: int | None = None, **fields: object) -> BlokError:
        """Refuse at ``offset`` (into the whole source), the current token's when left out."""
        line, column = self.lexer.locate(self.token.offset if offset is None else offset)
        return BlokError(code, line=line, column=column, **fields)

    def advance(self) -> Token:
        token = self.token
        self.index += 1
        return token

    def is_op(self, *values: str) -> bool:
        return self.token.kind == "op" and self.token.value in values

    def expect(self, value: str) -> Token:
        if not self.is_op(value):
            raise self.unexpected(f"expected {value!r}")
        return self.advance()

    def unexpected(self, expected: str) -> BlokError:
        token = self.token
        if token.kind == "end":
            return self.fail("expr.syntax", detail=f"{expected}, but the expression ends")
        if token.kind == "op":
            refused = self._refused(token)
            if refused is not None:
                return refused
        return self.fail("expr.syntax", detail=f"{expected}, not {token.value!r}")

    def _refused(self, token: Token) -> BlokError | None:
        """The code of an operator the language refuses by design."""
        value = token.value
        if value in _ARITHMETIC:
            return self.fail("expr.arithmetic", operator=value, function=_ARITHMETIC[value])
        if value == "=>":
            return self.fail("expr.lambda")
        if value == "=":
            return self.fail("callback.assignment")
        if value == ";":
            return self.fail("callback.sequence")
        if value == "[":
            return self.fail("expr.subscript")
        if value in ("===", "!=="):
            return self.fail("expr.syntax", detail=f"write {value[:2]}: equality is always deep")
        if value == "?.":
            return self.fail("expr.syntax", detail="?. is not supported; missing paths read as null")
        return None

    # -- entry points

    def callback(self) -> Expr:
        if self.token.kind == "name" and self.token.value == "await":
            raise self.fail("callback.await")
        expression = self.expression()
        if self.token.kind == "name" and self.token.value == "into":
            into = self.advance()
            if not isinstance(expression, Call):
                raise self.fail("callback.into", into.offset)
            name = self.token
            if name.kind != "name" or self.peek().kind != "end":
                raise self.fail("callback.into", into.offset)
            self.advance()
            return Into(call=expression, name=name.value, offset=into.offset)
        self.finish()
        return expression

    def finish(self) -> None:
        if self.token.kind != "end":
            raise self.unexpected("expected the end of the expression")

    # -- grammar

    def expression(self) -> Expr:
        return self.ternary()

    def ternary(self) -> Expr:
        cond = self.logical("??")
        if not self.is_op("?"):
            return cond
        offset = self.advance().offset
        then = self.expression()
        self.expect(":")
        otherwise = self.expression()
        return Ternary(cond=cond, then=then, otherwise=otherwise, offset=offset)

    def logical(self, operator: str) -> Expr:
        inner = (lambda: self.logical("||")) if operator == "??" else (lambda: self.logical("&&")) if operator == "||" else self.equality
        first = inner()
        operands = [first]
        offset = first.offset
        while self.is_op(operator):
            self.advance()
            operands.append(inner())
        if len(operands) == 1:
            return first
        return Logical(operator=operator, operands=tuple(operands), offset=offset)

    def equality(self) -> Expr:
        left = self.relation()
        if not self.is_op("==", "!="):
            return left
        operator = self.advance()
        right = self.relation()
        if self.is_op("==", "!=", ">", ">=", "<", "<="):
            raise self.fail("expr.chained_comparison")
        return Compare(operator=operator.value, left=left, right=right, offset=operator.offset)

    def relation(self) -> Expr:
        left = self.unary()
        if not self.is_op(">", ">=", "<", "<="):
            return left
        operator = self.advance()
        right = self.unary()
        if self.is_op(">", ">=", "<", "<="):
            raise self.fail("expr.chained_comparison")
        return Compare(operator=operator.value, left=left, right=right, offset=operator.offset)

    def unary(self) -> Expr:
        if self.is_op("!"):
            offset = self.advance().offset
            return Not(operand=self.unary(), offset=offset)
        if self.is_op("-"):
            offset = self.advance().offset
            if self.token.kind != "number":
                raise self.fail("expr.arithmetic", offset, operator="-", function="neg")
            number = self.advance()
            value = number.literal
            assert isinstance(value, (int, float))
            return Literal(value=-value, offset=offset)
        return self.postfix(self.primary())

    def postfix(self, expression: Expr) -> Expr:
        if self.is_op("["):
            raise self.fail("expr.subscript")
        if self.is_op("(") and not isinstance(expression, Call):
            raise self.fail("expr.syntax", detail="only a path can be called")
        if isinstance(expression, Call) and self.is_op("."):
            raise self.fail("expr.syntax", detail="a call's result has no fields; read them from its record")
        return expression

    def primary(self) -> Expr:
        token = self.token
        if token.kind in ("number", "string"):
            self.advance()
            return Literal(value=token.literal, offset=token.offset)
        if token.kind == "name":
            if token.value in ("true", "false"):
                self.advance()
                return Literal(value=token.value == "true", offset=token.offset)
            if token.value == "null":
                self.advance()
                return Literal(value=None, offset=token.offset)
            if token.value in ("undefined", "function", "new", "await", "async", "typeof", "this", "let", "const", "var"):
                raise self.fail("expr.syntax", detail=f"{token.value!r} is not part of the blok language")
            return self.path_or_call()
        if token.kind == "op":
            if token.value == "(":
                self.advance()
                inner = self.expression()
                self.expect(")")
                if self.is_op("=>"):
                    raise self.fail("expr.lambda")
                return inner
            if token.value == "[":
                return self.list_literal()
            if token.value == "{":
                return self.object_literal()
        raise self.unexpected("expected a value")

    def path_or_call(self) -> Expr:
        first = self.advance()
        segments = [first.value]
        while self.is_op("."):
            self.advance()
            segment = self.token
            if segment.kind != "name":
                raise self.unexpected("expected a name after '.'")
            segments.append(self.advance().value)
        if self.is_op("=>"):
            raise self.fail("expr.lambda")
        if not self.is_op("("):
            return Path(segments=tuple(segments), offset=first.offset)
        self.advance()
        arguments: list[Argument] = []
        while not self.is_op(")"):
            arguments.append(self.argument())
            if not self.is_op(","):
                break
            self.advance()
        self.expect(")")
        return Call(callee=tuple(segments), arguments=tuple(arguments), offset=first.offset)

    def argument(self) -> Argument:
        token = self.token
        if token.kind == "name" and self.peek().kind == "op" and self.peek().value == ":":
            self.advance()
            self.advance()
            return Argument(name=token.value, value=self.expression(), offset=token.offset)
        if token.kind == "name" and self.peek().kind == "op" and self.peek().value == "=":
            raise self.fail("expr.syntax", self.peek().offset, detail=f"name an argument with ':' ({token.value}: ...)")
        return Argument(name=None, value=self.expression(), offset=token.offset)

    def list_literal(self) -> ListExpr:
        offset = self.advance().offset
        items: list[Expr] = []
        while not self.is_op("]"):
            items.append(self.expression())
            if not self.is_op(","):
                break
            self.advance()
        self.expect("]")
        return ListExpr(items=tuple(items), offset=offset)

    def object_literal(self) -> ObjectExpr:
        offset = self.advance().offset
        entries: list[tuple[str, Expr]] = []
        seen: set[str] = set()
        while not self.is_op("}"):
            key = self.token
            if key.kind == "name":
                name = key.value
            elif key.kind == "string" and isinstance(key.literal, str) and key.literal:
                name = key.literal
            else:
                raise self.unexpected("expected a key (a name or a non-empty string)")
            self.advance()
            if name in seen:
                raise self.fail("expr.duplicate_key", key.offset, key=name)
            seen.add(name)
            self.expect(":")
            entries.append((name, self.expression()))
            if not self.is_op(","):
                break
            self.advance()
        self.expect("}")
        return ObjectExpr(entries=tuple(entries), offset=offset)


def parse_expression(text: str, *, base: int = 0, locate: Locator | None = None) -> Expr:
    """Parse a value expression (no ``into``).

    Args:
        text: The expression.
        base: Where ``text`` starts in the source ``locate`` reads, for error positions.
        locate: The source's locator; ``text`` itself when left out.

    Raises:
        BlokError: With an ``expr.*`` or ``callback.*`` code.
    """
    parser = _Parser(text, base, locate or Locator(text))
    if parser.token.kind == "end":
        raise parser.fail("expr.syntax", detail="the expression is empty")
    expression = parser.expression()
    parser.finish()
    return expression


def parse_attribute(text: str, *, base: int = 0, locate: Locator | None = None) -> Expr:
    """Parse an attribute's expression: a value, or a callback (``<call> into <name>``).

    Raises:
        BlokError: With an ``expr.*`` or ``callback.*`` code.
    """
    parser = _Parser(text, base, locate or Locator(text))
    if parser.token.kind == "end":
        raise parser.fail("expr.syntax", detail="the expression is empty")
    return parser.callback()


__all__ = [
    "Argument",
    "Call",
    "Compare",
    "Expr",
    "Into",
    "ListExpr",
    "Literal",
    "Locator",
    "Logical",
    "Not",
    "ObjectExpr",
    "Path",
    "Scalar",
    "Ternary",
    "parse_attribute",
    "parse_expression",
]
