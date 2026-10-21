"""The ``bdf`` namespace on polars frames.

Importing this module registers ``df.bdf`` on ``polars.DataFrame`` and
``polars.LazyFrame``. It imports polars alone, so ``import bdf`` can register
it cheaply whenever polars is already loaded; the resolution machinery is
imported on first use.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Generic, TypeVar

import polars as pl

FrameT = TypeVar("FrameT", pl.DataFrame, pl.LazyFrame)


@pl.api.register_dataframe_namespace("bdf")
@pl.api.register_lazyframe_namespace("bdf")
class BDFNamespace(Generic[FrameT]):
    """Select BDF columns by name, whether recorded or derivable.

    Every method accepts preferred labels (``"Voltage / mV"``) or
    machine-readable names (``"voltage_mV"``); see :mod:`bdf.columns`.

    Examples:
        >>> import polars as pl
        >>> import bdf.columns
        >>> df = pl.DataFrame({"Current / A": [1.0, -1.0], "Voltage / V": [4.0, 3.0]})
        >>> df.bdf.with_columns("power_watt").filter(pl.col("power_watt") > 0).height
        1
    """

    def __init__(self, df: FrameT) -> None:
        self._df = df

    def _resolve(self, columns: tuple[str | Iterable[str], ...]) -> list[pl.Expr]:
        from bdf.columns import _flatten, resolve

        return list(resolve(_flatten(columns), self._df.collect_schema().names()).values())

    def col(self, name: str) -> pl.Expr:
        """Expression for one BDF column, for use in this frame's own methods.

        Args:
            name: Requested column name.

        Returns:
            Polars expression aliased to ``name``.

        Examples:
            >>> import polars as pl
            >>> import bdf.columns
            >>> df = pl.DataFrame({"Current / A": [0.5, 2.0]})
            >>> df.filter(df.bdf.col("current_mA") > 1000).height
            1
        """
        return self._resolve((name,))[0]

    def with_columns(self, *columns: str | Iterable[str]) -> FrameT:
        """This frame with the requested BDF columns added.

        Args:
            *columns: Requested column names, or iterables of them.

        Returns:
            A frame of the same kind.
        """
        return self._df.with_columns(self._resolve(columns))

    def select(self, *columns: str | Iterable[str]) -> FrameT:
        """Only the requested BDF columns of this frame, in the requested order.

        Args:
            *columns: Requested column names, or iterables of them.

        Returns:
            A frame of the same kind.
        """
        return self._df.select(self._resolve(columns))
