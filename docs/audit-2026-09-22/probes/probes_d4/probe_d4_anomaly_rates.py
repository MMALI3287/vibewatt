"""d4 probe: anomaly false-positive rate by weekly activity using the real implementation."""

from __future__ import annotations

import random
from datetime import date, timedelta

import pytest

from ccburn.analysis import anomaly

START = date(2026, 6, 1)  # Monday


@pytest.mark.parametrize("k", range(1, 8))
def test_fp_rate_by_days_per_week(k):
    rng = random.Random(7)
    rows = []
    for i in range(365):
        d = START + timedelta(days=i)
        if d.weekday() < k:
            rows.append({"day": d.isoformat(), "cost": round(rng.lognormvariate(1.5, 0.35), 2)})
    findings, _ = anomaly.detect(rows, None, START + timedelta(days=364))
    evaluable = sum(1 for r in rows if (date.fromisoformat(r["day"]) - START).days >= 14)
    print(f"\n{k} days/week, no injected spikes: {len(findings)}/{evaluable} flagged ({len(findings)/evaluable:.1%})")
