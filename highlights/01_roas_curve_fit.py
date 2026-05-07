# ============================================================
# Highlight: D7 ROAS Predictor — Logarithmic Curve Fitting
# Source: featured/roas_predictor/predictor.py
#
# Why this matters:
#   D7 ROAS is the key profitability signal for mobile ad campaigns,
#   but you must wait 7 days to measure it directly. By then a bad
#   campaign may already have burned thousands of dollars.
#
#   This module forecasts D7 from D0~D6 settled data using
#   ROAS(t) = a * ln(t) + b — fitting the diminishing-returns curve.
#   Gives ~3-5 days of advance warning.
#
# Things to notice:
#   - Settlement-aware data prep: a "day d" data point is only
#     trustworthy after stat_time >= (d+1)*24 hours
#   - Confidence scoring (high / medium / insufficient)
#   - R² goodness-of-fit reported per prediction
#   - Defensive guards: bad fits with pred<=0 or pred>10 are rejected
# ============================================================

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit
from typing import Optional


# ---- The model ---------------------------------------------------

def _log_model(x, a, b):
    return a * np.log(x) + b


def fit_and_predict(settled: dict) -> tuple[Optional[float], Optional[float], str]:
    """
    Input  settled: {day_index: roas_value}, where 0=D0 ... 7=D7
    x-axis: 1-indexed (D0 -> x=1, D7 -> x=8)
    Returns: (predicted_d7, r2, confidence)
        confidence: 'high' (>=4 pts), 'medium' (2-3), 'insufficient' (<2)
    """
    days = sorted(k for k, v in settled.items() if v > 0)
    if len(days) < 2:
        return None, None, 'insufficient'

    x = np.array([d + 1 for d in days], dtype=float)
    y = np.array([settled[d] for d in days], dtype=float)

    try:
        popt, _ = curve_fit(_log_model, x, y, p0=[0.3, y[0]], maxfev=3000)
    except Exception:
        return None, None, 'insufficient'

    pred = _log_model(8.0, *popt)
    if pred <= 0 or pred > 10:           # sanity guard against pathological fits
        return None, None, 'insufficient'

    # R² goodness of fit
    y_pred = _log_model(x, *popt)
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2 = float(1 - ss_res / ss_tot) if ss_tot > 0 else 0.0

    confidence = 'high' if len(days) >= 4 else 'medium'
    return float(pred), r2, confidence


# ---- Settlement-aware data preparation ---------------------------

def campaign_settled_roas(df: pd.DataFrame, platform: str,
                           campaign: str, window_days: int = 5) -> tuple[dict, float]:
    """
    Build the settled ROAS series for a campaign over its most recent
    `window_days` cohorts.

    Key insight: each cohort is "settled" up to a different day depending
    on its age. A 3-day-old cohort has reliable D0~D2 data only.
    We use the *minimum* settlement age across the window so every day
    in the output is computed from the SAME set of cohorts — guaranteeing
    monotonic ROAS_D0 < ROAS_D1 < ... (which the curve fit relies on).
    """
    mask = (df['media_source'] == platform) & (df['campaign'] == campaign) & (df['cost'] > 0)
    camp = df[mask].copy()
    if camp.empty:
        return {}, 0.0

    rev_cols = [c for c in [f'revenue_sum_day{d}' for d in range(8)] if c in camp.columns]
    agg_dict = {'cost': 'sum', 'stat_time': 'max'}
    for c in rev_cols:
        agg_dict[c] = 'sum'
    camp = camp.groupby('cohort_day').agg(agg_dict).reset_index()

    # Need at least D0~D3 settled (stat_time >= 96h) for fit to be meaningful
    camp = camp[camp['stat_time'] >= 96]
    if camp.empty:
        return {}, 0.0

    camp = camp.sort_values('cohort_day', ascending=False).head(window_days)

    # day d is fully settled once stat_time >= (d+1)*24
    # use the *minimum* stat_time across the window for a consistent denominator
    min_st = int(camp['stat_time'].min())
    max_settled_day = min(min_st // 24 - 1, 7)

    total_cost = camp['cost'].sum()
    settled = {}
    for d in range(max_settled_day + 1):
        rev = camp[f'revenue_sum_day{d}'].sum()
        if rev > 0:
            settled[d] = float(rev / total_cost)

    return settled, float(total_cost)
