"""Blok parsing, validation and dependency inference."""

from arkitekt_spec.declare.blok.from_svg import SvgConversionError, SvgConversionWarning, from_svg
from arkitekt_spec.declare.blok.parser import (
    BlokParser,
    PortCallParser,
    bsx,
    coerce_util_call,
    parse_util_call,
)
from arkitekt_spec.declare.blok.registry import build_declared_bloks, demo_state_for
from arkitekt_spec.declare.blok.unparse import to_bsx
from arkitekt_spec.declare.blok.validate import (
    CatalogSource,
    DependencyIndex,
    catalogs_view,
    check_context_root,
    local_roots_of,
    resolve_state_reference,
    validate_blok,
    validate_bsx,
)

__all__ = [
    "BlokParser",
    "CatalogSource",
    "DependencyIndex",
    "PortCallParser",
    "SvgConversionError",
    "SvgConversionWarning",
    "bsx",
    "build_declared_bloks",
    "catalogs_view",
    "check_context_root",
    "coerce_util_call",
    "demo_state_for",
    "from_svg",
    "local_roots_of",
    "parse_util_call",
    "resolve_state_reference",
    "to_bsx",
    "validate_blok",
    "validate_bsx",
]
