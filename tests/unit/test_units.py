"""Unit handling: the pint registry, aliases, and get_unit_conversion."""

from __future__ import annotations

from fractions import Fraction

import pytest

from bdf import spec, units
from bdf.units import get_unit_conversion


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Ohm", "ohm"),
        ("Cel", "degC"),
        ("A.h", "Ah"),
        ("W.h", "Wh"),
    ],
)
def test_normalize_unit_ucum_codes_are_case_sensitive(raw: str, expected: str) -> None:
    """schema:unitCode UCUM codes normalize to BDF's canonical pint-valid unit string.

    Lookup is case-sensitive: "Ohm" maps to lowercase "ohm" (pint doesn't
    recognize capitalized "Ohm"), while "Cel"/"A.h"/"W.h" map to their
    spelled-out canonical forms.
    """
    assert units._normalize_unit(raw) == expected


@pytest.mark.parametrize(
    ("alias", "canonical", "expected"),
    [
        # Identical unit string
        ("V", "V", True),
        # UCUM dotted form pint already parses as a product of two units,
        # equal in value to the canonical spelling
        ("A.h", "Ah", True),
        # Already a built-in pint alias for degree_Celsius
        ("celsius", "degC", True),
        # Same dimensionality as degC (both temperature) but not the same
        # value at a given magnitude, since kelvin has no offset
        ("kelvin", "degC", False),
        # Not a unit pint knows at all
        ("nonexistentunit123", "V", False),
    ],
)
def test_pint_understands(alias: str, canonical: str, expected: bool) -> None:
    """_pint_understands compares actual conversion values at 0 and 1, not just dimensionality."""
    assert units._pint_understands(units._get_ureg(), alias, canonical) is expected


def test_pint_understands_never_propagates_on_pathological_alias() -> None:
    """U+2103 (℃) must yield a bool, never raise -- so reg construction can't crash.

    pint's parser handles this character inconsistently across platforms: on
    some it parses ℃ natively as degree_Celsius (returning True), on others it
    raises a bare AssertionError rather than UndefinedUnitError. The truth value
    is therefore platform-dependent; what matters is that _pint_understands
    swallows the failure and returns a bool instead of letting it escape.
    """
    assert isinstance(units._pint_understands(units._get_ureg(), "℃", "degC"), bool)


@pytest.mark.parametrize(
    ("src", "dst", "expected"),
    [
        # Identity
        ("V", "V", (1.0, 0.0)),
        ("v", "V", (1.0, 0.0)),  # vendor casing, registered in _UNIT_ALIAS
        ("AH", "Ah", (1.0, 0.0)),  # Digatron casing, registered in _UNIT_ALIAS
        # Case-sensitive: SI prefixes differ by case
        ("MW", "mW", (1.0e9, 0.0)),
        ("mW", "MW", (1.0e-9, 0.0)),
        # Scale-only
        ("V", "mV", (1000.0, 0.0)),
        ("kV", "V", (1000.0, 0.0)),
        ("s", "ms", (1000.0, 0.0)),
        ("Ah", "mAh", (1000.0, 0.0)),
        ("mWh", "Wh", (0.001, 0.0)),
        # Scale + offset (temperature)
        ("degC", "K", (1.0, 273.15)),
        ("K", "degC", (1.0, -273.15)),
        # Incompatible dimensions
        ("V", "A", None),
        ("s", "V", None),
        # Dimensionless / None handling
        (None, "1", (1.0, 0.0)),
        (None, "", (1.0, 0.0)),
        ("", "1", (1.0, 0.0)),
        ("1", "1", (1.0, 0.0)),
        (None, "V", None),
        ("V", "1", None),
        # Bare "C"/"c" disambiguated as Celsius only against a temperature dst
        ("C", "degC", (1.0, 0.0)),
        ("c", "degC", (1.0, 0.0)),
        ("C", "K", (1.0, 273.15)),
        ("C", "degF", (Fraction(9, 5), 32)),
        # Bare "C" against a non-temperature dst is interpreted as coulombs
        ("C", "A", None),
        # Bare "C" as the dst is not special-cased (only src is); falls through
        # to pint, which treats it as coulombs, an incompatible dimension here
        ("degC", "C", None),
        # degC alt-spelling aliases registered with pint via "@alias" must
        # preserve the +273.15 offset, not just the dimensionality
        ("degc", "K", (1.0, 273.15)),
        ("degreec", "K", (1.0, 273.15)),
        ("\xf8c", "K", (1.0, 273.15)),
        ("\xb0c", "K", (1.0, 273.15)),
        # "Ohm" (UCUM unitCode casing) is registered as a real pint alias, so
        # conversions to a *different* resistance unit work too -- not just
        # the case-insensitive identity short-circuit above
        ("Ohm", "kohm", (0.001, 0.0)),
        ("Ohm", "milliohm", (1000.0, 0.0)),
        # "Cel" (UCUM unitCode casing) converts with the correct offset
        ("Cel", "K", (1.0, 273.15)),
        ("Cel", "degC", (1.0, 0.0)),
        # Units with special characters
        ("℃", "degC", (1.0, 0.0)),
        ("°C", "degC", (1.0, 0.0)),
        ("mA*h", "Ah", (1.0e-3, 0.0)),
        ("mA·h", "Ah", (1.0e-3, 0.0)),
        ("mA h", "Ah", (1.0e-3, 0.0)),
        ("mA  h", "Ah", (1.0e-3, 0.0)),
        ("V s⁻¹", "V/s", (1.0, 0.0)),
        ("V / s", "V/s", (1.0, 0.0)),
        ("V s^-1", "V/s", (1.0, 0.0)),
        ("µA", "A", (1.0e-6, 0.0)),
        ("μA", "A", (1.0e-6, 0.0)),
        ("Ω", "ohm", (1.0, 0.0)),
        ("Ω", "ohm", (1.0, 0.0)),
    ],
)
def test_get_unit_conversion(src: str | None, dst: str, expected: tuple[float, float] | None) -> None:
    result = get_unit_conversion(src, dst)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx((float(expected[0]), float(expected[1])))


@pytest.mark.parametrize(
    ("src", "dst", "expected"),
    [
        ("s", "h", (1 / 3600, 0.0)),
        ("ps", "h", (1e-12 / 3600, 0.0)),
        ("ms", "s", (0.001, 0.0)),
        ("degF", "degC", (5 / 9, -160 / 9)),
        ("degC", "degF", (9 / 5, 32.0)),
    ],
)
def test_get_unit_conversion_keeps_full_precision(src: str, dst: str, expected: tuple[float, float]) -> None:
    """Factors keep their significant digits: no rounding, no cancellation in the scale."""
    result = get_unit_conversion(src, dst)
    assert result is not None
    scale, offset = result
    assert scale == pytest.approx(expected[0], rel=1e-15)
    # The offset is pint's own conversion of zero, accurate to a few ulp.
    assert offset == pytest.approx(expected[1], rel=1e-14, abs=1e-12)


def test_get_unit_conversion_none_dst_none_src() -> None:
    """get_unit_conversion(None, None) returns (1.0, 0.0)."""
    assert get_unit_conversion(None, None) == (1.0, 0.0)


def test_get_unit_conversion_physical_src_none_dst() -> None:
    """get_unit_conversion('V', None) returns None."""
    assert get_unit_conversion("V", None) is None


def test_spec_reexports_unit_conversion() -> None:
    """bdf.spec keeps get_unit_conversion and ureg, which it published before bdf.units existed."""
    assert spec.get_unit_conversion is get_unit_conversion
    assert spec.ureg is units.ureg
