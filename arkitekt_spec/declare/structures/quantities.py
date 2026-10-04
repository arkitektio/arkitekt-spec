"""Bridge between kanne's pint-backed dimension types and ``PortKind.QUANTITY`` ports.

kanne is an optional dependency (the ``units`` extra). A kanne dimension type
(``Duration``, ``ElectricPotential``, ...) declares a canonical ``reference_unit`` and
knows its dimensionality; we use that to stamp a quantity port's ``unit``/``dimension``
at definition time and to expand a wire string (``"5 mV"``) back into a live quantity.

Everything here degrades gracefully when kanne is absent: :func:`is_pint_quantity`
returns ``False`` so no quantity ports are ever produced, and :func:`resolve_quantity_type`
returns ``None`` so the serializer can raise a clear error.

kanne is never imported from here. It brings pint, and with it a large part of the
scientific stack, which an app with no quantity pays for on every start. A class can
only be a kanne type if whoever wrote it imported kanne, so kanne being loaded is
both the question and the answer.
"""

import importlib.util
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kanne import PintQuantity


def _pint_quantity(load: bool = False) -> Any:  # noqa: ANN401
    """kanne's ``PintQuantity``, when kanne is loaded; ``None`` otherwise.

    ``load`` imports an installed kanne that nobody imported yet. That is for a
    *value* arriving off the wire -- a caller reading a quantity an action
    returned never named a kanne type itself -- and never for asking what a
    class is, which is what every app does on every start.
    """
    module = sys.modules.get("kanne")
    if module is None and load and importlib.util.find_spec("kanne") is not None:
        import kanne as module  # pyright: ignore[reportMissingImports]
    return getattr(module, "PintQuantity", None)


def _registry() -> Any:  # noqa: ANN401
    """kanne's unit registry. Only called once kanne is known to be loaded."""
    from kanne.registry import get_global_registry

    return get_global_registry()


def is_pint_quantity(cls: Any) -> bool:
    """Whether ``cls`` is a kanne dimension type (a :class:`~kanne.PintQuantity` subclass).

    Always ``False`` when kanne isn't installed, so callers need no separate guard.
    """
    quantity = _pint_quantity()
    return quantity is not None and isinstance(cls, type) and issubclass(cls, quantity)


def dimension_of(cls: "type[PintQuantity]") -> str:
    """The pint dimensionality string of a kanne type, e.g. ``"[time]"``.

    This is the wiring-compatibility key stamped onto the port's ``dimension`` field.
    """
    return str(_registry().get_dimensionality(cls.reference_unit))


def proposed_units_of(cls: "type[PintQuantity]") -> "tuple[str, ...]":
    """Units a UI should propose for a kanne type: its declared ``proposed_units``,
    falling back to just its ``reference_unit`` when the type declares none."""
    proposed = tuple(getattr(cls, "proposed_units", ()) or ())
    return proposed or (cls.reference_unit,)


def _iter_quantity_types() -> "list[type[PintQuantity]]":
    """All (transitive) concrete kanne dimension types currently imported."""
    quantity = _pint_quantity(load=True)
    if quantity is None:
        return []
    seen: set = set()
    stack = [quantity]
    out: list = []
    while stack:
        current = stack.pop()
        for sub in current.__subclasses__():
            if sub not in seen:
                seen.add(sub)
                stack.append(sub)
                out.append(sub)
    return out


#: Canonical ``reference_unit`` (unique per dimension) → kanne type. Populated on first
#: lookup and refreshed on a miss so kanne types defined after import are still found.
_REFERENCE_UNIT_TO_TYPE: "dict[str, type[PintQuantity]]" = {}


def _refresh_map() -> None:
    for cls in _iter_quantity_types():
        reference_unit = getattr(cls, "reference_unit", "")
        if reference_unit:  # the base PintQuantity has an empty reference_unit
            _REFERENCE_UNIT_TO_TYPE[reference_unit] = cls


def resolve_quantity_type(unit: str | None) -> "type[PintQuantity] | None":
    """The kanne type whose ``reference_unit`` equals ``unit`` (a port's ``unit``), or ``None``.

    ``None`` when kanne is absent, ``unit`` is falsy, or no kanne type declares that
    reference unit — the caller turns that into an explicit expansion error.
    """
    if not unit or _pint_quantity(load=True) is None:
        return None
    if unit not in _REFERENCE_UNIT_TO_TYPE:
        _refresh_map()
    return _REFERENCE_UNIT_TO_TYPE.get(unit)


def shrink_quantity(value: Any) -> str:
    """Render a QUANTITY value as its abbreviated pint string for the wire (``"5 mV"``).

    Accepts a kanne :class:`~kanne.PintQuantity`, a raw ``pint.Quantity`` (formatted with
    the abbreviated ``~`` spec), or an already-serialized string.
    """
    quantity = _pint_quantity()
    if quantity is not None and isinstance(value, quantity):
        return value.to_pint_string()
    if isinstance(value, str):
        return value
    try:
        return f"{value:~}"  # raw pint.Quantity abbreviated format
    except (TypeError, ValueError):
        return str(value)


def expand_quantity(value: Any, reference_unit: str | None) -> Any:
    """Parse a wire value into a live kanne quantity of the type identified by ``reference_unit``.

    Raises :class:`ValueError` if kanne isn't installed or no kanne type declares
    ``reference_unit`` as its reference unit — the serializer surfaces that as a port error.
    """
    cls = resolve_quantity_type(reference_unit)
    if cls is None:
        if importlib.util.find_spec("kanne") is None:
            raise ValueError(
                "Cannot expand a QUANTITY port: kanne is not installed. Install the "
                "'units' extra (e.g. `pip install rekuest[units]`)."
            )
        raise ValueError(
            "Cannot expand a QUANTITY port: no kanne dimension type has reference unit "
            f"{reference_unit!r}."
        )
    return cls.validate(value)


def matches_dimension(value: Any, dimension: str | None) -> bool:
    """Whether ``value`` is a quantity whose pint dimensionality equals ``dimension``.

    Used to predicate a QUANTITY port (e.g. for union disambiguation) and, conceptually,
    to gate wiring. Accepts a kanne :class:`~kanne.PintQuantity`, a raw ``pint.Quantity``,
    or a unit-bearing pint string (``"5 mV"``). ``False`` when kanne is absent, ``dimension``
    is ``None``, or the value can't be interpreted as a matching quantity.
    """
    quantity_type = _pint_quantity(load=True) if dimension is not None else None
    if quantity_type is None or dimension is None:
        return False
    registry = _registry()
    try:
        if isinstance(value, quantity_type):
            quantity = value.quantity
        elif isinstance(value, str):
            quantity = registry(value)
        elif getattr(value, "dimensionality", None) is not None:
            quantity = value  # a raw pint.Quantity
        else:
            return False
        return str(quantity.dimensionality) == dimension
    except Exception:
        return False
