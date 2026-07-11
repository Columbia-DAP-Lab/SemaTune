#!/usr/bin/env python3
"""Shared helpers for optional plot error bars."""

from __future__ import annotations

import math
from typing import Optional, Sequence, Tuple

import numpy as np


def factor_error_bounds(
    factors: Sequence[float],
    center_factor: Optional[float],
    unit: str = "pct",
) -> Optional[Tuple[float, float]]:
    """Return asymmetric +/-1σ error bars for positive multiplicative factors.

    The spread is computed in log space so it matches geomean-style aggregation.
    Returned values are distances below and above the provided center in the
    requested display unit.
    """
    if center_factor is None or not math.isfinite(center_factor) or center_factor <= 0:
        return None

    vals = np.array(
        [float(v) for v in factors if v is not None and math.isfinite(float(v)) and float(v) > 0],
        dtype=float,
    )
    if vals.size < 2:
        return None

    sigma = float(np.std(np.log(vals), ddof=1))
    if not math.isfinite(sigma) or sigma <= 0:
        return None

    lower_factor = center_factor * math.exp(-sigma)
    upper_factor = center_factor * math.exp(sigma)

    if unit == "factor":
        return center_factor - lower_factor, upper_factor - center_factor
    if unit != "pct":
        raise ValueError(f"Unsupported unit: {unit}")

    center_pct = (center_factor - 1.0) * 100.0
    lower_pct = (lower_factor - 1.0) * 100.0
    upper_pct = (upper_factor - 1.0) * 100.0
    return center_pct - lower_pct, upper_pct - center_pct
