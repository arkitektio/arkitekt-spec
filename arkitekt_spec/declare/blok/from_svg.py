"""Convert an SVG file into a logo blok: :func:`from_svg`.

A logo blok draws with the components of the standard catalog's logo context
(``SVG``/``ActionLogo``, ``G``, ``Path``, ``Circle``, ... ``LinearGradient``,
``ClipPath``), which are a subset of SVG under camelCase names. Designers hand over
``.svg`` files written by Inkscape, Illustrator or Figma, so this translates what the
subset can draw and drops the rest, saying what it dropped:

- the cascade is flattened: presentation attributes, then ``<style>`` rules (simple
  selectors only: ``tag``, ``.class``, ``#id`` and their compounds), then ``style=""``;
- ``<use>`` is expanded in place, a ``<symbol>`` or nested ``<svg>`` becomes a ``G``
  with the transform its viewBox implies;
- gradients and clip paths are emitted once each, under their own ids, in one
  ``Defs`` at the top, and only when something references them; a gradient's
  ``href`` chain is resolved into its attributes and stops;
- paint on the root ``<svg>`` (``fill="none" stroke="currentColor"``, as icon sets
  write) moves to a ``G`` around the drawing, since the root takes no paint;
- ``currentColor`` paints ``ink``; an action logo, which paints only with
  ``LOGO_PAINT_TOKENS``, has every colour mapped to the nearest token;
- masks, filters, patterns, images, markers, scripts and event handlers have no
  component and are dropped.

The result is BSX source, so ``app.blok(name, from_svg(path), context="logo")`` sends
it as source. It is checked before it is returned: it parses back into the tree it was
printed from, and passes the logo checks registration runs.
"""

import colorsys
import functools
import os
import re
import warnings
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from arkitekt_spec.actions import ComponentContext, ComponentNodeInput, ComponentPropInput
from arkitekt_spec.declare.blok.parser import bsx
from arkitekt_spec.declare.blok.unparse import to_bsx
from arkitekt_spec.declare.blok.validate import (
    check_context_root,
    check_logo_complexity,
    check_logo_palette,
)
from arkitekt_spec.declare.catalogs import (
    ACTION_LOGO_CONTEXT,
    CONTEXT_ROOTS,
    LOGO_CONTEXTS,
    LOGO_PAINT_TOKENS,
    load_standard_catalog,
)


class SvgConversionWarning(UserWarning):
    """Part of the SVG has no logo component and was dropped or approximated."""


class SvgConversionError(ValueError):
    """The SVG cannot become a logo blok (or, under ``strict``, not without loss)."""


def from_svg(
    svg: str | bytes | os.PathLike[str],
    *,
    context: ComponentContext | str = ComponentContext.LOGO,
    title: str | None = None,
    palette: Mapping[str, str] | None = None,
    precision: int | None = None,
    strict: bool = False,
) -> str:
    """The BSX source of a logo blok that draws ``svg``.

    Args:
        svg: The SVG document as text (``str`` or ``bytes``), or the path of a file.
            Text and path are told apart by type: a ``str`` is never read as a path.
        context: ``logo`` (root ``SVG``, any colour) or ``action_logo`` (root
            ``ActionLogo``, palette tokens only).
        title: The logo's title; the SVG's own ``<title>`` when left out.
        palette: Colours to paint with a token, e.g. ``{"#0f172a": "ink"}``. Keys are
            any CSS colour, values ``LOGO_PAINT_TOKENS``. Applies in both contexts; in
            an action logo a colour it does not name goes to the nearest token.
        precision: Round the numbers in ``d``, ``points`` and transforms to this many
            decimals, to fit a detailed drawing into the path data a logo may hold.
        strict: Raise on anything that would be dropped or approximated, instead of
            warning with :class:`SvgConversionWarning`.

    Returns:
        BSX source. Pass it to ``app.blok(name, ..., context=...)``. For an inline
        ``@app.action(logo=...)``, which reads a string as a blok *name*, parse it
        first: ``logo=bsx(from_svg(path, context="action_logo"))``.

    Raises:
        SvgConversionError: If the SVG cannot be read, or the logo it converts to
            breaks a logo limit (components, depth, path data), or -- under
            ``strict`` -- anything is lost.
    """
    context_name = str(getattr(context, "value", context)).upper()
    if context_name not in LOGO_CONTEXTS:
        raise ValueError(
            f"from_svg makes logo bloks: context is one of {LOGO_CONTEXTS}, not {context!r}"
        )
    if precision is not None and precision < 0:
        raise ValueError(f"precision is a number of decimals, not {precision!r}")

    root = _parse(svg)
    converter = _Converter(
        root,
        action=context_name == ACTION_LOGO_CONTEXT,
        palette=_palette(palette or {}),
        precision=precision,
        strict=strict,
    )
    tree = _finish(
        converter.convert(CONTEXT_ROOTS[context_name], title), CONTEXT_ROOTS[context_name]
    )

    source = to_bsx(tree)
    if bsx(source) != tree:  # pragma: no cover - a converter bug, not an input error
        raise SvgConversionError(
            "the converted logo does not parse back into the tree it was printed from"
        )
    try:
        check_context_root(tree, context_name)
        check_logo_complexity(tree)
        if converter.action:
            check_logo_palette(tree)
    except ValueError as error:
        hint = ""
        if "path data" in str(error) and precision is None:
            hint = "; pass precision=2 (or fewer decimals) to round it"
        raise SvgConversionError(
            f"the converted logo is too much for a logo: {error}{hint}"
        ) from error

    for message in converter.warnings:
        warnings.warn(message, SvgConversionWarning, stacklevel=2)
    return source


# --------------------------------------------------------------------------- reading

SVG_NAMESPACE = "http://www.w3.org/2000/svg"
XLINK_NAMESPACE = "http://www.w3.org/1999/xlink"
XML_NAMESPACE = "http://www.w3.org/XML/1998/namespace"


def _parse(svg: str | bytes | os.PathLike[str]) -> ET.Element:
    if isinstance(svg, os.PathLike):
        data: str | bytes = Path(svg).read_bytes()
    elif isinstance(svg, (str, bytes)):
        data = svg
    else:
        raise TypeError(f"from_svg reads SVG text (str, bytes) or a path, not {type(svg).__name__}")

    # Entities are how an XML file expands itself (the billion laughs); an SVG never needs one.
    if (data.find(b"<!ENTITY") if isinstance(data, bytes) else data.find("<!ENTITY")) >= 0:
        raise SvgConversionError("the SVG declares XML entities, which are refused")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise SvgConversionError(f"the SVG is not well-formed XML: {error}") from error
    if _local(root.tag) != "svg" or _namespace(root.tag) not in ("", SVG_NAMESPACE):
        raise SvgConversionError(f"the document's root is <{_local(root.tag)}>, not <svg>")
    return root


def _namespace(name: str) -> str:
    return name[1:].split("}", 1)[0] if name.startswith("{") else ""


def _local(name: str) -> str:
    return name.rsplit("}", 1)[-1]


def _is_svg(element: ET.Element) -> bool:
    return _namespace(element.tag) in ("", SVG_NAMESPACE)


# --------------------------------------------------------------------------- vocabulary

COMPONENTS = {
    "g": "G",
    "a": "G",
    "switch": "G",
    "path": "Path",
    "circle": "Circle",
    "ellipse": "Ellipse",
    "rect": "Rect",
    "line": "Line",
    "polyline": "Polyline",
    "polygon": "Polygon",
    "text": "SvgText",
}
"""The SVG elements drawn in place, and the component each becomes."""

RESOURCES = {
    "linearGradient": "LinearGradient",
    "radialGradient": "RadialGradient",
    "clipPath": "ClipPath",
}
"""The SVG elements emitted into ``Defs`` when referenced."""

QUIET_ELEMENTS = frozenset(
    {"title", "desc", "metadata", "style", "defs", "symbol", "stop", *RESOURCES}
)
"""Elements that draw nothing in place, read elsewhere or not at all, and lose nothing."""

LOST_ELEMENTS = frozenset(
    {
        "mask",
        "filter",
        "pattern",
        "image",
        "marker",
        "foreignObject",
        "script",
        "animate",
        "animateMotion",
        "animateTransform",
        "set",
        "textPath",
        "view",
        "cursor",
    }
)
"""Elements with no logo component: dropped, with a warning."""

NOT_LOGO_PROPS = frozenset({"onClick", "animate", "animateDelay"})
"""Props of the logo components an SVG file has no spelling for."""

QUIET_ATTRIBUTES = frozenset(
    {
        "id",
        "class",
        "style",
        "version",
        "baseProfile",
        "enable-background",
        "xml:space",
        "shape-rendering",
        "text-rendering",
        "image-rendering",
        "color-rendering",
        "color-interpolation",
        "color-interpolation-filters",
        "color",
        "display",
        "visibility",
        "overflow",
        "role",
        "focusable",
        "systemLanguage",
        "requiredFeatures",
        "requiredExtensions",
        "pointer-events",
        "cursor",
        "href",
        "target",
        "xmlns",
    }
)
"""Attributes that change nothing a logo draws: dropped without a warning."""

TEXT_ATTRIBUTES = {
    "font-family": "fontFamily",
    "font-size": "fontSize",
    "font-weight": "fontWeight",
    "text-anchor": "textAnchor",
    "dominant-baseline": "dominantBaseline",
}
"""Text attributes that inherit: set on a group, they are carried down to its texts."""

ENUMS = {
    "fillRule": ("nonzero", "evenodd"),
    "strokeLinecap": ("butt", "round", "square"),
    "strokeLinejoin": ("miter", "round", "bevel"),
    "textAnchor": ("start", "middle", "end"),
    "dominantBaseline": ("auto", "middle", "central", "hanging", "alphabetic"),
    "gradientUnits": ("userSpaceOnUse", "objectBoundingBox"),
    "clipPathUnits": ("userSpaceOnUse", "objectBoundingBox"),
}
"""The values Orkestrator's logo renderer accepts for its enumerated props."""

ENUM_ALIASES = {("strokeLinejoin", "miter-clip"): "miter", ("strokeLinejoin", "arcs"): "miter"}

LENGTHS = frozenset(
    {
        "cx",
        "cy",
        "r",
        "rx",
        "ry",
        "x",
        "y",
        "width",
        "height",
        "x1",
        "y1",
        "x2",
        "y2",
        "fx",
        "fy",
        "fontSize",
        "strokeWidth",
    }
)
OPACITIES = frozenset({"opacity", "fillOpacity", "strokeOpacity", "stopOpacity"})
PAINTS = {"fill": "fillOpacity", "stroke": "strokeOpacity", "stopColor": "stopOpacity"}
"""Each paint prop, and the opacity prop a colour's alpha moves to when it becomes a token."""
TRANSFORMS = frozenset({"transform", "gradientTransform"})

UNITS = {"px": 1.0, "pt": 4 / 3, "pc": 16.0, "mm": 96 / 25.4, "cm": 96 / 2.54, "in": 96.0}

NUMBER = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")
URL = re.compile(r"url\(\s*['\"]?#([^'\")\s]+)['\"]?\s*\)\s*(.*)$", re.DOTALL)


@functools.cache
def _logo_props() -> dict[str, frozenset[str]]:
    """The props each logo component takes, from the standard catalog."""
    return {
        component.name: frozenset(prop.key for prop in component.props) - NOT_LOGO_PROPS
        for component in load_standard_catalog().components
        if component.context == "LOGO" or component.children_context == "LOGO"
    }


def _camel(name: str) -> str:
    return re.sub(r"-([a-z])", lambda match: match.group(1).upper(), name)


# --------------------------------------------------------------------------- the tree


@dataclass
class _Node:
    component: str
    props: dict[str, str] = field(default_factory=dict)
    children: list["_Node"] = field(default_factory=list)
    id: str | None = None
    """An explicit id: kept only where ``url(#id)`` names the node."""


def _finish(node: _Node, node_id: str) -> ComponentNodeInput:
    """The wire tree, with the ids :func:`bsx` would give it: structural unless explicit."""
    seen: dict[str, int] = {}
    children = []
    for child in node.children:
        index = seen.get(child.component, 0)
        seen[child.component] = index + 1
        children.append(_finish(child, child.id or f"{node_id}/{child.component}[{index}]"))
    return ComponentNodeInput(
        id=node_id,
        component=node.component,
        props=tuple(
            ComponentPropInput(key=key, static_value=value) for key, value in node.props.items()
        )
        or None,
        children=tuple(children) or None,
    )


# --------------------------------------------------------------------------- css


@dataclass(frozen=True)
class _Rule:
    tag: str | None
    classes: tuple[str, ...]
    id: str | None
    specificity: tuple[int, int, int, int]
    declarations: tuple[tuple[str, str], ...]

    def matches(self, element: ET.Element) -> bool:
        if self.tag is not None and self.tag != _local(element.tag):
            return False
        if self.id is not None and self.id != element.get("id"):
            return False
        return set(self.classes) <= set((element.get("class") or "").split())


SELECTOR = re.compile(r"(?P<tag>[A-Za-z][\w-]*|\*)?(?P<rest>(?:[.#][\w-]+)*)")


def _declarations(text: str) -> list[tuple[str, str]]:
    found = []
    for declaration in text.split(";"):
        name, colon, value = declaration.partition(":")
        value = value.replace("!important", "").strip()
        if colon and name.strip() and value:
            found.append((name.strip().lower(), value))
    return found


# --------------------------------------------------------------------------- colours

NAMED_COLOURS = dict(
    re.findall(
        r"(\w+):(\w+)",
        "aliceblue:f0f8ff antiquewhite:faebd7 aqua:00ffff aquamarine:7fffd4 azure:f0ffff "
        "beige:f5f5dc bisque:ffe4c4 black:000000 blanchedalmond:ffebcd blue:0000ff "
        "blueviolet:8a2be2 brown:a52a2a burlywood:deb887 cadetblue:5f9ea0 chartreuse:7fff00 "
        "chocolate:d2691e coral:ff7f50 cornflowerblue:6495ed cornsilk:fff8dc crimson:dc143c "
        "cyan:00ffff darkblue:00008b darkcyan:008b8b darkgoldenrod:b8860b darkgray:a9a9a9 "
        "darkgreen:006400 darkgrey:a9a9a9 darkkhaki:bdb76b darkmagenta:8b008b "
        "darkolivegreen:556b2f darkorange:ff8c00 darkorchid:9932cc darkred:8b0000 "
        "darksalmon:e9967a darkseagreen:8fbc8f darkslateblue:483d8b darkslategray:2f4f4f "
        "darkslategrey:2f4f4f darkturquoise:00ced1 darkviolet:9400d3 deeppink:ff1493 "
        "deepskyblue:00bfff dimgray:696969 dimgrey:696969 dodgerblue:1e90ff firebrick:b22222 "
        "floralwhite:fffaf0 forestgreen:228b22 fuchsia:ff00ff gainsboro:dcdcdc ghostwhite:f8f8ff "
        "gold:ffd700 goldenrod:daa520 gray:808080 green:008000 greenyellow:adff2f grey:808080 "
        "honeydew:f0fff0 hotpink:ff69b4 indianred:cd5c5c indigo:4b0082 ivory:fffff0 khaki:f0e68c "
        "lavender:e6e6fa lavenderblush:fff0f5 lawngreen:7cfc00 lemonchiffon:fffacd "
        "lightblue:add8e6 lightcoral:f08080 lightcyan:e0ffff lightgoldenrodyellow:fafad2 "
        "lightgray:d3d3d3 lightgreen:90ee90 lightgrey:d3d3d3 lightpink:ffb6c1 lightsalmon:ffa07a "
        "lightseagreen:20b2aa lightskyblue:87cefa lightslategray:778899 lightslategrey:778899 "
        "lightsteelblue:b0c4de lightyellow:ffffe0 lime:00ff00 limegreen:32cd32 linen:faf0e6 "
        "magenta:ff00ff maroon:800000 mediumaquamarine:66cdaa mediumblue:0000cd "
        "mediumorchid:ba55d3 mediumpurple:9370db mediumseagreen:3cb371 mediumslateblue:7b68ee "
        "mediumspringgreen:00fa9a mediumturquoise:48d1cc mediumvioletred:c71585 "
        "midnightblue:191970 mintcream:f5fffa mistyrose:ffe4e1 moccasin:ffe4b5 "
        "navajowhite:ffdead navy:000080 oldlace:fdf5e6 olive:808000 olivedrab:6b8e23 "
        "orange:ffa500 orangered:ff4500 orchid:da70d6 palegoldenrod:eee8aa palegreen:98fb98 "
        "paleturquoise:afeeee palevioletred:db7093 papayawhip:ffefd5 peachpuff:ffdab9 "
        "peru:cd853f pink:ffc0cb plum:dda0dd powderblue:b0e0e6 purple:800080 "
        "rebeccapurple:663399 red:ff0000 rosybrown:bc8f8f royalblue:4169e1 saddlebrown:8b4513 "
        "salmon:fa8072 sandybrown:f4a460 seagreen:2e8b57 seashell:fff5ee sienna:a0522d "
        "silver:c0c0c0 skyblue:87ceeb slateblue:6a5acd slategray:708090 slategrey:708090 "
        "snow:fffafa springgreen:00ff7f steelblue:4682b4 tan:d2b48c teal:008080 thistle:d8bfd8 "
        "tomato:ff6347 turquoise:40e0d0 violet:ee82ee wheat:f5deb3 white:ffffff "
        "whitesmoke:f5f5f5 yellow:ffff00 yellowgreen:9acd32",
    )
)
"""The CSS named colours, as ``rrggbb``."""

RGBA = tuple[float, float, float, float]


def _colour(value: str) -> RGBA | None:
    """``value`` as red, green, blue and alpha in 0..1, or ``None`` if it is no CSS colour."""
    text = value.strip().lower()
    if text in NAMED_COLOURS:
        text = "#" + NAMED_COLOURS[text]
    if re.fullmatch(r"#[0-9a-f]{3,4}", text):
        text = "#" + "".join(digit * 2 for digit in text[1:])
    if re.fullmatch(r"#[0-9a-f]{6}([0-9a-f]{2})?", text):
        channels = [int(text[i : i + 2], 16) / 255 for i in range(1, len(text), 2)]
        return (channels[0], channels[1], channels[2], channels[3] if len(channels) == 4 else 1.0)

    match = re.fullmatch(r"(rgba?|hsla?)\((.*)\)", text)
    if match is None:
        return None
    parts = [part for part in re.split(r"[\s,/]+", match.group(2).strip()) if part]
    if len(parts) not in (3, 4):
        return None
    try:
        alpha = _fraction(parts[3]) if len(parts) == 4 else 1.0
        if match.group(1).startswith("rgb"):
            red, green, blue = (
                _fraction(part) if part.endswith("%") else float(part) / 255 for part in parts[:3]
            )
        else:
            hue = float(parts[0].removesuffix("deg")) / 360 % 1
            red, green, blue = colorsys.hls_to_rgb(hue, _fraction(parts[2]), _fraction(parts[1]))
    except ValueError:
        return None
    return (_clamp(red), _clamp(green), _clamp(blue), _clamp(alpha))


def _fraction(part: str) -> float:
    return float(part[:-1]) / 100 if part.endswith("%") else float(part)


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def _hex(colour: RGBA) -> str:
    return "#" + "".join(f"{round(channel * 255):02x}" for channel in colour[:3])


def _token(colour: RGBA) -> str:
    """The palette token nearest ``colour``: greys by lightness, colours by lightness of the brand."""
    red, green, blue, alpha = colour
    if alpha == 0:
        return "none"
    _, lightness, _ = colorsys.rgb_to_hls(red, green, blue)
    if max(red, green, blue) - min(red, green, blue) < 0.12:
        return "ink" if lightness < 0.3 else "surface" if lightness > 0.85 else "muted"
    return "brand-strong" if lightness < 0.35 else "brand-soft" if lightness > 0.75 else "brand"


def _palette(palette: Mapping[str, str]) -> dict[str, str]:
    resolved = {}
    for colour, token in palette.items():
        rgba = _colour(colour)
        if rgba is None:
            raise ValueError(f"palette key {colour!r} is not a CSS colour")
        if token not in LOGO_PAINT_TOKENS:
            raise ValueError(f"palette value {token!r} is not one of {LOGO_PAINT_TOKENS}")
        resolved[_hex(rgba)] = token
    return resolved


# --------------------------------------------------------------------------- numbers


def _number(value: float, decimals: int = 6) -> str:
    text = f"{value:.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _short(value: float, decimals: int) -> str:
    """``value`` in as few characters as path data allows: ``0.5`` is ``.5``."""
    text = _number(value, decimals)
    if text.startswith("0."):
        return text[1:]
    if text.startswith("-0."):
        return "-" + text[2:]
    return text


def _round_path(d: str, decimals: int) -> str:
    """Path data with every number rounded, arc flags (``a1 1 0 011 1``) read as flags."""
    tokens: list[str] = []
    command = ""
    index = 0
    position = 0
    while position < len(d):
        char = d[position]
        if char in " ,\t\r\n":
            position += 1
        elif char.isalpha():
            if char not in "MmZzLlHhVvCcSsQqTtAa":
                raise ValueError(f"{char!r} is no path command")
            tokens.append(char)
            command, index = char, 0
            position += 1
        elif command in ("A", "a") and index % 7 in (3, 4) and char in "01":
            tokens.append(char)
            index += 1
            position += 1
        else:
            match = NUMBER.match(d, position)
            if match is None:
                raise ValueError(f"cannot read path data at {d[position : position + 12]!r}")
            tokens.append(_short(float(match.group()), decimals))
            index += 1
            position = match.end()

    out = ""
    for token in tokens:
        if out and not out[-1].isalpha() and not token.isalpha() and not token.startswith("-"):
            out += " "
        out += token
    return out


def _round_numbers(text: str, decimals: int) -> str:
    return NUMBER.sub(lambda match: _number(float(match.group()), decimals), text)


def _collapse(value: str) -> str:
    return " ".join(value.split())


# --------------------------------------------------------------------------- converting


class _Converter:
    def __init__(
        self,
        root: ET.Element,
        *,
        action: bool,
        palette: Mapping[str, str],
        precision: int | None,
        strict: bool,
    ) -> None:
        self.root = root
        self.action = action
        self.palette = palette
        self.precision = precision
        self.strict = strict
        self.warnings: list[str] = []
        self.props = _logo_props()
        self.by_id = {element.get("id"): element for element in root.iter() if element.get("id")}
        self.rules = self._stylesheet()
        self.resources: dict[str, _Node | None] = {}
        """Gradients and clip paths by id, in the order first referenced; ``None`` while being built."""

    # ----------------------------------------------------------------- reporting

    def warn(self, message: str) -> None:
        if self.strict:
            raise SvgConversionError(message)
        if message not in self.warnings:
            self.warnings.append(message)

    # ----------------------------------------------------------------- the root

    def convert(self, root_component: str, title: str | None) -> _Node:
        declared = self.declared(self.root)
        root = _Node(root_component)

        viewbox = declared.pop("viewBox", None)
        if viewbox is None:
            width = _plain_number(declared.get("width"))
            height = _plain_number(declared.get("height"))
            if width is not None and height is not None:
                viewbox = f"0 0 {_number(width)} {_number(height)}"
            else:
                self.warn(
                    "the SVG has neither a viewBox nor a width and height in px; it is drawn unscaled"
                )
        if viewbox is not None:
            root.props["viewBox"] = _collapse(viewbox.replace(",", " "))
        aspect = declared.pop("preserveAspectRatio", None)
        if aspect:
            root.props["preserveAspectRatio"] = _collapse(aspect)
        title = title if title is not None else self.title_of(self.root)
        if title:
            self.set_text_prop(root, "title", title)
        for name in ("x", "y", "width", "height"):
            declared.pop(name, None)  # the slot sizes a logo

        # The root takes no paint: what an icon set puts there goes on a group around the drawing.
        # An action logo's group is ``ink`` first, since SVG's initial fill is black.
        drawing = _Node("G", {"fill": "ink"} if self.action else {})
        inherited = self.apply(drawing, "svg", declared, {})
        drawing.children = self.children(self.root, inherited, ())
        content = _simplify([drawing])

        if self.resources:
            defs = _Node(
                "Defs", children=[node for node in self.resources.values() if node is not None]
            )
            root.children.append(defs)
        root.children.extend(content)
        return root

    # ----------------------------------------------------------------- the cascade

    def _stylesheet(self) -> list[_Rule]:
        rules: list[_Rule] = []
        order = 0
        for style in self.root.iter():
            if _local(style.tag) != "style" or not _is_svg(style):
                continue
            text = re.sub(r"/\*.*?\*/", "", "".join(style.itertext()), flags=re.DOTALL)
            position = 0
            while (opening := text.find("{", position)) >= 0:
                depth, closing = 1, opening + 1
                while closing < len(text) and depth:
                    depth += {"{": 1, "}": -1}.get(text[closing], 0)
                    closing += 1
                prelude, body = text[position:opening], text[opening + 1 : closing - 1]
                position = closing
                if ";" in prelude:  # a statement at-rule (@import ...;) before this block
                    prelude = prelude.rsplit(";", 1)[1]
                prelude = prelude.strip()
                if prelude.startswith("@"):
                    self.warn(f"the CSS at-rule {prelude.split()[0]!r} is not applied")
                    continue
                declarations = tuple(_declarations(body))
                for selector in prelude.split(","):
                    selector = selector.strip()
                    match = SELECTOR.fullmatch(selector)
                    if not selector or match is None:
                        self.warn(
                            f"the CSS selector {selector!r} is not applied: only tag, .class and #id are"
                        )
                        continue
                    tag = match.group("tag")
                    rest = re.findall(r"[.#][\w-]+", match.group("rest"))
                    ids = [part[1:] for part in rest if part[0] == "#"]
                    classes = tuple(part[1:] for part in rest if part[0] == ".")
                    order += 1
                    rules.append(
                        _Rule(
                            tag=None if tag in (None, "*") else tag,
                            classes=classes,
                            id=ids[0] if len(ids) == 1 else None if not ids else "\0",
                            specificity=(
                                len(ids),
                                len(classes),
                                int(tag not in (None, "*")),
                                order,
                            ),
                            declarations=declarations,
                        )
                    )
        return rules

    def declared(self, element: ET.Element) -> dict[str, str]:
        """The element's attributes after the cascade, by their SVG names."""
        declared: dict[str, str] = {}
        for name, value in element.attrib.items():
            namespace = _namespace(name)
            if namespace == XLINK_NAMESPACE and _local(name) == "href":
                declared["href"] = value
            elif namespace == XML_NAMESPACE:
                declared[f"xml:{_local(name)}"] = value
            elif namespace == "":
                declared[name] = value
        for rule in sorted(
            (rule for rule in self.rules if rule.matches(element)),
            key=lambda rule: rule.specificity,
        ):
            declared.update(rule.declarations)
        declared.update(_declarations(declared.pop("style", "")))
        return declared

    # ----------------------------------------------------------------- elements

    def children(
        self, element: ET.Element, inherited: Mapping[str, str], using: tuple[str, ...]
    ) -> list[_Node]:
        return [node for child in element for node in self.element(child, inherited, using)]

    def element(
        self, element: ET.Element, inherited: Mapping[str, str], using: tuple[str, ...]
    ) -> list[_Node]:
        if not isinstance(element.tag, str) or not _is_svg(element):
            return []  # comments, and editor elements (sodipodi:namedview, rdf:RDF)
        tag = _local(element.tag)
        if tag in QUIET_ELEMENTS:
            return []
        if tag in LOST_ELEMENTS:
            self.warn(f"<{tag}> has no logo component and was dropped")
            return []

        declared = self.declared(element)
        if declared.get("display") == "none" or declared.get("visibility") in (
            "hidden",
            "collapse",
        ):
            return []

        if tag == "use":
            return self.use(element, declared, inherited, using)
        if tag == "svg":
            return self.nested_svg(element, declared, inherited, using)
        component = COMPONENTS.get(tag)
        if component is None:
            self.warn(f"<{tag}> is not an SVG element a logo draws and was dropped")
            return []

        node = _Node(component)
        own_title = self.title_of(element)
        if own_title:
            self.set_text_prop(node, "title", own_title)
        if component == "SvgText":
            return self.text(element, node, declared, inherited)
        inner = self.apply(node, tag, declared, inherited)
        if component == "G":
            node.children = self.children(element, inner, using)
        return [node]

    def text(
        self,
        element: ET.Element,
        node: _Node,
        declared: dict[str, str],
        inherited: Mapping[str, str],
    ) -> list[_Node]:
        content = _collapse("".join(self.text_of(element, top=True)))
        if not content:
            return []
        for name in ("x", "y"):
            values = declared.get(name, "").replace(",", " ").split()
            if len(values) > 1:
                self.warn(f"<text {name}> lists a position per glyph; only the first is kept")
                declared[name] = values[0]
        for name in ("dx", "dy", "rotate"):
            if name in declared:
                self.warn(f"<text {name}> has no logo prop and was dropped")
                declared.pop(name)
        self.apply(node, "text", declared, inherited)
        for name, key in TEXT_ATTRIBUTES.items():
            if key not in node.props and name in inherited:
                self.set_prop(node, "text", key, inherited[name])
        self.set_text_prop(node, "text", content)
        return [node] if "text" in node.props else []

    def text_of(self, element: ET.Element, top: bool = False) -> Iterator[str]:
        if not top and any(name in element.attrib for name in ("x", "y", "dx", "dy")):
            self.warn("<tspan> positions have no logo prop; the text runs on as one line")
        yield element.text or ""
        for child in element:
            if isinstance(child.tag, str) and _local(child.tag) in ("tspan", "a"):
                yield from self.text_of(child)
            elif isinstance(child.tag, str) and _local(child.tag) not in ("title", "desc"):
                self.warn(f"<{_local(child.tag)}> inside <text> was dropped")
            yield child.tail or ""

    def use(
        self,
        element: ET.Element,
        declared: dict[str, str],
        inherited: Mapping[str, str],
        using: tuple[str, ...],
    ) -> list[_Node]:
        href = declared.pop("href", "")
        if not href.startswith("#"):
            self.warn(f"<use href={href!r}> names no element of this file and was dropped")
            return []
        target = self.by_id.get(href[1:])
        if target is None:
            self.warn(f"<use href={href!r}> names no element and was dropped")
            return []
        if href[1:] in using:
            self.warn(f"<use href={href!r}> uses itself and was dropped")
            return []

        x = _plain_number(declared.pop("x", None)) or 0.0
        y = _plain_number(declared.pop("y", None)) or 0.0
        width, height = declared.pop("width", None), declared.pop("height", None)
        steps = [declared.pop("transform", "")]
        if x or y:
            steps.append(f"translate({_number(x)} {_number(y)})")

        node = _Node("G")
        inner = self.apply(node, "use", declared, inherited)
        using = (*using, href[1:])
        target_tag = _local(target.tag)
        if target_tag in ("symbol", "svg"):
            target_declared = self.declared(target)
            steps.append(
                self.viewbox_transform(
                    target_declared.get("viewBox"),
                    width or target_declared.get("width"),
                    height or target_declared.get("height"),
                    target_declared.get("preserveAspectRatio"),
                )
            )
            inner = self.apply(
                _Node("G"), target_tag, _only(target_declared, TEXT_ATTRIBUTES), inner
            )
            node.children = self.children(target, inner, using)
        else:
            node.children = self.element(target, inner, using)
        self.set_transform(node, steps)
        return [node]

    def nested_svg(
        self,
        element: ET.Element,
        declared: dict[str, str],
        inherited: Mapping[str, str],
        using: tuple[str, ...],
    ) -> list[_Node]:
        x = _plain_number(declared.pop("x", None)) or 0.0
        y = _plain_number(declared.pop("y", None)) or 0.0
        steps = [f"translate({_number(x)} {_number(y)})" if x or y else ""]
        steps.append(
            self.viewbox_transform(
                declared.pop("viewBox", None),
                declared.pop("width", None),
                declared.pop("height", None),
                declared.pop("preserveAspectRatio", None),
            )
        )
        node = _Node("G")
        inner = self.apply(node, "svg", declared, inherited)
        node.children = self.children(element, inner, using)
        self.set_transform(node, steps)
        return [node]

    def viewbox_transform(
        self, viewbox: str | None, width: str | None, height: str | None, aspect: str | None
    ) -> str:
        """The transform that maps a viewBox onto a ``width`` x ``height`` viewport."""
        if not viewbox:
            return ""
        try:
            min_x, min_y, box_width, box_height = (
                float(part) for part in viewbox.replace(",", " ").split()
            )
        except ValueError:
            self.warn(f"cannot read viewBox {viewbox!r}; it was ignored")
            return ""
        port_width, port_height = _plain_number(width), _plain_number(height)
        if port_width is None or port_height is None or not box_width or not box_height:
            self.warn(
                "a <symbol> or nested <svg> is drawn at the size of its viewBox: it has no width and height in px"
            )
            port_width, port_height = box_width, box_height

        scale_x, scale_y = port_width / box_width, port_height / box_height
        offset_x = offset_y = 0.0
        align = (aspect or "xMidYMid").split()[0]
        if align != "none":
            if aspect and "slice" in aspect:
                self.warn(
                    "preserveAspectRatio 'slice' on a <symbol> or nested <svg> is drawn as 'meet'"
                )
            scale_x = scale_y = min(scale_x, scale_y)
            slack_x, slack_y = port_width - box_width * scale_x, port_height - box_height * scale_y
            offset_x = slack_x * (0.5 if "xMid" in align else 1.0 if "xMax" in align else 0.0)
            offset_y = slack_y * (0.5 if "YMid" in align else 1.0 if "YMax" in align else 0.0)

        steps = []
        if offset_x or offset_y:
            steps.append(f"translate({_number(offset_x)} {_number(offset_y)})")
        if scale_x != 1 or scale_y != 1:
            steps.append(
                f"scale({_number(scale_x)})"
                if scale_x == scale_y
                else f"scale({_number(scale_x)} {_number(scale_y)})"
            )
        if min_x or min_y:
            steps.append(f"translate({_number(-min_x)} {_number(-min_y)})")
        return " ".join(steps)

    # ----------------------------------------------------------------- resources

    def reference(self, ref: str, kinds: tuple[str, ...]) -> bool:
        """Emit the gradient or clip path ``ref`` names into ``Defs``; whether it is one of ``kinds``."""
        if ref in self.resources:
            return True
        target = self.by_id.get(ref)
        if target is None:
            self.warn(f"url(#{ref}) names no element; the paint was dropped")
            return False
        tag = _local(target.tag)
        if tag not in kinds:
            self.warn(
                f"url(#{ref}) names a <{tag}>, which a logo cannot paint with; it was dropped"
            )
            return False
        self.resources[ref] = None  # a clip path that clips itself ends here
        self.resources[ref] = (
            self.clip_path(target, ref) if tag == "clipPath" else self.gradient(target, ref)
        )
        return True

    def gradient(self, element: ET.Element, ref: str) -> _Node:
        tag = _local(element.tag)
        component = RESOURCES[tag]
        chain = [element]
        while (href := self.declared(chain[-1]).get("href", "")).startswith("#"):
            parent = self.by_id.get(href[1:])
            if (
                parent is None
                or parent in chain
                or _local(parent.tag) not in ("linearGradient", "radialGradient")
            ):
                break
            chain.append(parent)

        node = _Node(component, id=ref)
        allowed = self.props[component]
        for depth, link in enumerate(chain):
            same_kind = _local(link.tag) == tag
            for name, value in self.declared(link).items():
                key = _camel(name)
                inheritable = same_kind or key in ("gradientUnits", "gradientTransform")
                if key in allowed and key not in node.props and inheritable:
                    self.set_prop(node, tag, key, value)
                elif depth > 0 or key in allowed or name in QUIET_ATTRIBUTES:
                    continue
                elif name == "spreadMethod":
                    if value != "pad":
                        self.warn(f"spreadMethod={value!r} has no logo prop; the gradient pads")
                else:
                    self.warn(f"'{name}' on <{tag}> has no logo prop and was dropped")

        stops = next(
            (
                [
                    child
                    for child in link
                    if isinstance(child.tag, str) and _local(child.tag) == "stop"
                ]
                for link in chain
                if any(isinstance(child.tag, str) and _local(child.tag) == "stop" for child in link)
            ),
            [],
        )
        for stop in stops:
            stop_node = _Node("Stop")
            self.apply(stop_node, "stop", self.declared(stop), {})
            if self.action and "stopColor" not in stop_node.props:
                stop_node.props["stopColor"] = "ink"  # the initial stop-color is black
            node.children.append(stop_node)
        return node

    def clip_path(self, element: ET.Element, ref: str) -> _Node:
        declared = self.declared(element)
        node = _Node("ClipPath", id=ref)
        transform = declared.pop("transform", None)
        self.apply(node, "clipPath", declared, {})
        children = self.children(element, {}, ())
        if transform:
            group = _Node("G", children=children)
            self.set_transform(group, [transform])
            children = [group]
        node.children = _simplify(children)
        return node

    # ----------------------------------------------------------------- props

    def apply(
        self, node: _Node, tag: str, declared: Mapping[str, str], inherited: Mapping[str, str]
    ) -> dict[str, str]:
        """Set ``declared`` on ``node`` as its component's props; the text attributes its children inherit."""
        allowed = self.props[node.component]
        inner = dict(inherited)
        for name, value in declared.items():
            key = _camel(name)
            if key in allowed:
                self.set_prop(node, tag, key, value)
            elif name in TEXT_ATTRIBUTES:
                inner[name] = value
            elif name in QUIET_ATTRIBUTES or name.startswith(("data-", "aria-", "xml:", "xmlns")):
                continue
            elif name.startswith("on"):
                self.warn(f"the event handler '{name}' was dropped")
            else:
                self.warn(f"'{name}' on <{tag}> has no logo prop and was dropped")
        return inner

    def set_prop(self, node: _Node, tag: str, key: str, raw: str) -> None:
        value = _collapse(raw)
        if not value or value in ("inherit", "unset", "initial"):
            return
        if key in PAINTS:
            self.set_paint(node, key, value)
            return
        if key == "clipPath":
            match = URL.fullmatch(value)
            if value == "none":
                return
            if match and self.reference(match.group(1), ("clipPath",)):
                node.props[key] = f"url(#{match.group(1)})"
            elif not match:
                self.warn(f"clip-path={value!r} is not a url(#id) and was dropped")
            return
        if key in ENUMS:
            value = ENUM_ALIASES.get((key, value), value)
            if value not in ENUMS[key]:
                self.warn(f"{key}={value!r} is not one of {ENUMS[key]} and was dropped")
                return
        elif key in OPACITIES:
            value = _number(_fraction(value)) if value.endswith("%") else value
        elif key in LENGTHS:
            value = self.length(value)
        elif key == "strokeDasharray":
            value = re.sub(r"(\d)px\b", r"\1", value)
        elif key == "d":
            value = self.rounded(value, _round_path)
        elif key == "points" or key in TRANSFORMS:
            value = self.rounded(value, _round_numbers)
        elif key in ("title", "text"):
            self.set_text_prop(node, key, value)
            return
        if value.startswith(("$", "@")):
            self.warn(f"{key}={value!r} reads as an expression in BSX and was dropped")
            return
        node.props[key] = value

    def set_text_prop(self, node: _Node, key: str, raw: str) -> None:
        value = _collapse(raw)
        if value.startswith(("$", "@")):
            self.warn(f"{key}={value!r} reads as an expression in BSX and was dropped")
        elif value:
            node.props[key] = value

    def set_transform(self, node: _Node, steps: list[str]) -> None:
        transform = " ".join(step for step in steps if step)
        if transform:
            self.set_prop(node, "g", "transform", transform)

    def set_paint(self, node: _Node, key: str, value: str) -> None:
        match = URL.fullmatch(value)
        if match is not None:
            kinds = ("linearGradient", "radialGradient")
            if self.reference(match.group(1), kinds):
                node.props[key] = f"url(#{match.group(1)})"
            elif match.group(2).strip():
                self.set_paint(node, key, match.group(2).strip())
            else:
                node.props[key] = "none"
            return

        lowered = value.lower()
        if lowered in ("none", "transparent"):
            node.props[key] = "none"
            return
        if lowered == "currentcolor":
            node.props[key] = "ink"
            return
        colour = _colour(value)
        if colour is None:
            self.warn(f"{key}={value!r} is not a colour a logo can paint and was dropped")
            return
        token = self.palette.get(_hex(colour))
        if token is None and not self.action:
            node.props[key] = value  # a logo keeps its own colours, as written
            return
        if token is None:
            token = _token(colour)
        node.props[key] = token
        if colour[3] < 1 and token != "none":
            opacity = PAINTS[key]
            previous = _plain_number(node.props.get(opacity))
            node.props[opacity] = _number(colour[3] * (1.0 if previous is None else previous), 4)

    def length(self, value: str) -> str:
        match = re.fullmatch(rf"({NUMBER.pattern})\s*([a-zA-Z%]*)", value)
        if match is None:
            return value
        number, unit = match.group(1), match.group(2).lower()
        if unit in ("", "%"):
            return value.replace(" ", "")
        if unit in UNITS:
            return _number(float(number) * UNITS[unit], 4)
        self.warn(f"the length {value!r} is relative to a font; it is kept as written")
        return value

    def rounded(self, value: str, rounder: Callable[[str, int], str]) -> str:
        if self.precision is None:
            return value
        try:
            return rounder(value, self.precision)
        except ValueError as error:
            self.warn(f"{error}; the value is kept unrounded")
            return value

    def title_of(self, element: ET.Element) -> str:
        for child in element:
            if isinstance(child.tag, str) and _is_svg(child) and _local(child.tag) == "title":
                return _collapse("".join(child.itertext()))
        return ""


def _only(declared: Mapping[str, str], names: Mapping[str, str]) -> dict[str, str]:
    return {name: value for name, value in declared.items() if name in names}


def _plain_number(value: str | None) -> float | None:
    """A length in user units (``12``, ``12px``), or ``None`` for anything relative."""
    if value is None:
        return None
    match = re.fullmatch(rf"\s*({NUMBER.pattern})\s*(px)?\s*", value)
    return float(match.group(1)) if match else None


# --------------------------------------------------------------------------- simplifying

INHERITED_PROPS = frozenset(
    {
        "fill",
        "fillOpacity",
        "fillRule",
        "stroke",
        "strokeWidth",
        "strokeOpacity",
        "strokeLinecap",
        "strokeLinejoin",
        "strokeDasharray",
    }
)
"""Props a child's own value overrides: merging a group into it, the child's wins."""


def _simplify(nodes: list[_Node]) -> list[_Node]:
    """Fewer, shallower groups, drawing the same: a logo is held to 200 components, 12 deep.

    A group without props gives its children to its parent; a group with one child
    hands its props down to it, where that changes nothing drawn.
    """
    props = _logo_props()
    out: list[_Node] = []
    for node in nodes:
        node.children = _simplify(node.children)
        if node.component == "G" and node.id is None:
            if not node.children:
                continue
            if not node.props:
                out.extend(node.children)
                continue
            if len(node.children) == 1 and _merge(
                node, node.children[0], props[node.children[0].component]
            ):
                out.append(node.children[0])
                continue
        out.append(node)
    return out


def _merge(group: _Node, child: _Node, allowed: frozenset[str]) -> bool:
    """Move ``group``'s props onto its only ``child``, if the child draws the same for it."""
    if not set(group.props) <= allowed:
        return False
    if "clipPath" in group.props and ("clipPath" in child.props or "transform" in child.props):
        return False  # a clip path is in the user space of the element it is set on
    if "title" in group.props and "title" in child.props:
        return False
    merged = dict(child.props)
    for key, value in group.props.items():
        if key == "transform" and "transform" in child.props:
            merged[key] = f"{value} {child.props[key]}"
        elif key == "opacity" and "opacity" in child.props:
            outer, inner = _plain_number(value), _plain_number(child.props[key])
            if outer is None or inner is None:
                return False
            merged[key] = _number(outer * inner, 4)
        elif key not in child.props:
            merged[key] = value
        elif key not in INHERITED_PROPS:
            return False
    child.props = merged
    return True
