"""Unit handling for BDF: the shared pint registry and unit conversion.

Every unit conversion in bdf goes through :func:`get_unit_conversion`, which
asks pint for an exact ``(scale, offset)`` pair. Unit strings are
case-sensitive, as SI prefixes are (``MW`` is not ``mW``). Spellings that pint
does not parse, from vendor headers and the ontology's UCUM codes, are listed
in ``_UNIT_ALIAS`` and registered with pint as aliases of the unit they mean.

pint is imported, and the registry built, on first use.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pint

    ureg: pint.UnitRegistry

__all__ = ["get_unit_conversion", "ureg"]

_UNIT_ALIAS = {
    "celsius": "degC",
    "degree_celsius": "degC",
    "℃": "degC",
    "degc": "degC",
    "degreec": "degC",
    "\xf8c": "degC",
    "\xf8C": "degC",
    "\xb0c": "degC",
    "\xb0C": "degC",
    "Sec": "second",
    # UCUM codes used in schema:unitCode annotations (case-sensitive, exact
    # casing as it appears in the ontology's schema:unitCode literals)
    "Cel": "degC",
    "A.h": "Ah",
    "W.h": "Wh",
    "Ohm": "ohm",
    "Ω": "ohm",  # 'ohm' character, 'Omega' character already understood by pint
    # Vendor casings that pint does not parse. Conversion is case-sensitive
    # (SI prefixes are: "MW" is not "mW"), so each such spelling is named here.
    "v": "V",
    "AH": "Ah",  # Digatron "AhCha#AH"
}

# Bare "C" or "c" is ambiguous (Celsius vs Coulombs). This set lists the BDF
# destination units that unambiguously identify a temperature quantity, allowing
# bare "C"/"c" to be treated as Celsius in get_unit_conversion.
_TEMPERATURE_DST_UNITS: frozenset[str] = frozenset({"degC", "°C", "K", "degF"})

_UNIT_CAPTURE = r"([A-Za-z0-9.·/*^%°℃ΩΩµμ⁰¹²³⁴⁵⁶⁷⁸⁹⁻ \-]+)"  # omega/ohm and mu/micro are different characters

_ureg: pint.UnitRegistry | None = None


def _pint_understands(reg: pint.UnitRegistry, alias: str, canonical: str) -> bool:
    """True if pint already parses `alias` to the same value as `canonical`.

    Args:
        reg: Registry to resolve both unit strings against.
        alias: Candidate unit string (e.g. a UCUM code).
        canonical: Known-good pint unit string to compare against.

    Returns:
        True if pint resolves `alias` to the same quantity as `canonical`
        both at 0 and 1 (catching offset units, not just dimensionality).
    """
    import pint

    try:
        a0, c0 = reg.Quantity(0, alias), reg.Quantity(0, canonical)
        a1, c1 = reg.Quantity(1, alias), reg.Quantity(1, canonical)
        return (
            abs(a0.to(c0.units).magnitude - c0.magnitude) < 1e-9
            and abs(a1.to(c1.units).magnitude - c1.magnitude) < 1e-9
        )
    except (pint.errors.PintError, AssertionError):
        # PintError covers undefined/incompatible units; AssertionError covers
        # strings pint's parser chokes on internally (e.g. "℃" / ℃), which
        # would otherwise escape and crash registry construction on pint >= 0.25.
        return False


def _get_ureg() -> pint.UnitRegistry:
    """Return the shared unit registry, build on first use then cache.

    Returns:
        UnitRegistry with the BDF unit aliases defined.
    """
    global _ureg
    if _ureg is None:
        import pint

        reg: pint.UnitRegistry = pint.UnitRegistry()
        for alias, canonical in _UNIT_ALIAS.items():
            if _pint_understands(reg, alias, canonical):
                continue
            # @alias adds another name to the existing unit (preserving offset
            # behavior for affine units like degC), unlike `name = degC`, which
            # would silently define a new non-offset unit.
            with contextlib.suppress(Exception):
                reg.define(f"@alias {canonical} = {alias}")
        _ureg = reg
    return _ureg


def __getattr__(name: str) -> Any:
    """Get lazily built module attributes."""
    if name == "ureg":
        return _get_ureg()
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


def _normalize_unit(unit: str) -> str:
    """Map known unit aliases (e.g. 'celsius') to canonical pint strings.

    Args:
        unit: Unit string to normalize.

    Returns:
        Canonical pint unit string.
    """
    key = unit.strip()
    if not key:
        return key
    return _UNIT_ALIAS.get(key, key)


def _resolve_bare_c(unit: str, reference_unit: str | None) -> str:
    """Read a bare ``"C"`` or ``"c"`` as Celsius where the other unit is a temperature.

    Bare ``"C"`` is otherwise coulomb, so the context decides.

    Args:
        unit: Unit string that may be a bare ``"C"``.
        reference_unit: The unit it converts to or from.

    Returns:
        ``"degC"`` for a bare ``"C"`` against a temperature unit, else ``unit``.
    """
    if unit in ("C", "c") and reference_unit in _TEMPERATURE_DST_UNITS:
        return "degC"
    return unit


def get_unit_conversion(src_unit: str | None, dst_unit: str | None) -> tuple[float, float] | None:
    """Return (scale, offset) for src→dst unit conversion, None if incompatible.

    Args:
        src_unit: Source unit string (or None for dimensionless).
        dst_unit: Destination unit string (or None for dimensionless).

    Returns:
        Tuple of (scale, offset) for conversion, or None if incompatible.
    """
    src_bare = _normalize_unit((src_unit or "").strip())
    dst_bare = _normalize_unit((dst_unit or "").strip())
    src_is_dim = src_bare in ("", "1")
    dst_is_dim = dst_bare in ("1", "")
    if src_is_dim or dst_is_dim:
        return (1.0, 0.0) if src_is_dim and dst_is_dim else None
    if src_bare == dst_bare:
        return (1.0, 0.0)
    src_bare = _resolve_bare_c(src_bare, dst_bare)

    import pint

    reg = _get_ureg()
    try:
        src_zero, dst_zero = reg.Quantity(0, src_bare), reg.Quantity(0, dst_bare)
        if src_zero.dimensionality != dst_zero.dimensionality:
            return None
        # Scale from a difference of two quantities, which pint gives in delta
        # units (delta_degC for degC), so offset units convert without the
        # cancellation of subtracting two converted values.
        src_step = reg.Quantity(1, src_bare) - src_zero
        dst_step_units = (reg.Quantity(1, dst_bare) - dst_zero).units
        scale = float(src_step.to(dst_step_units).magnitude)
        offset = float(src_zero.to(dst_zero.units).magnitude)
        return (scale, offset)
    except (pint.errors.PintError, AssertionError):
        return None
