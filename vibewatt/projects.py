"""Project names as shown: aliases and masking, applied the same way everywhere.

`project_aliases` merges raw names into one label; `mask_projects` replaces
labels with "project N". Before Phase 6.5e each endpoint did its own part of
this, so a shown name could not be used as a filter (A-029) and the session
endpoints leaked raw names (A-030). One mapping now serves every response and
every filter. Mask numbers follow all-time cost, so they do not change when a
filter changes.
"""

from __future__ import annotations


def project_map(conn, cfg: dict) -> dict[str, str]:
    """Raw project name -> the name every response shows."""
    aliases = cfg.get("project_aliases") or {}
    cost: dict[str, float] = {}
    for row in conn.execute(
        "SELECT project, COALESCE(SUM(cost), 0) FROM rollup GROUP BY project"
        " UNION ALL SELECT project, COALESCE(SUM(cost), 0) FROM sessions"
        " WHERE harvested = 1 AND project IS NOT NULL GROUP BY project"
    ):
        cost[row[0]] = cost.get(row[0], 0.0) + float(row[1])
    labels = {raw: str(aliases.get(raw, raw)) for raw in cost}
    if not cfg.get("mask_projects"):
        return labels
    by_label: dict[str, float] = {}
    for raw, label in labels.items():
        by_label[label] = by_label.get(label, 0.0) + cost[raw]
    ranked = sorted(by_label, key=lambda label: (-by_label[label], label))
    pseudonym = {label: f"project {i}" for i, label in enumerate(ranked, 1)}
    return {raw: pseudonym[label] for raw, label in labels.items()}


def resolve(mapping: dict[str, str], value: str | None, masked: bool) -> list[str] | None:
    """A shown name back to every raw name behind it. None means no filter.

    Unmasked, a raw name still matches itself, so an old link keeps working.
    Masked, only a pseudonym matches: a raw name must not reveal itself.
    """
    if not value:
        return None
    raws = sorted(raw for raw, shown in mapping.items() if shown == value)
    if raws or masked:
        return raws
    return [value]


def clause(column: str, raws: list[str] | str | None) -> tuple[str, list]:
    """`column IN (...)` for a resolved project filter, or nothing."""
    if raws is None:
        return "", []
    values = [raws] if isinstance(raws, str) else list(raws)
    if not values:
        return "0", []  # a masked name that matches nothing
    return f"{column} IN ({','.join('?' * len(values))})", values


def relabel_buckets(buckets: dict, mapping: dict[str, str]) -> dict:
    """Merge a {raw project: Bucket} dict under shown names."""
    from .aggregate import _merge_into

    out: dict = {}
    for raw, bucket in buckets.items():
        label = mapping.get(raw, raw)
        if label in out:
            _merge_into(out[label], bucket)
        else:
            out[label] = bucket
    return out
