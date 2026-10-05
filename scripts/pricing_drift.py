"""Compare the built-in rate table with the community table and report drift.

Run weekly in CI. Exit status 1 means a person should check the official
pricing page: an Anthropic model is missing from BUILTIN or a rate differs.
"""

from __future__ import annotations

import json
import urllib.request

from vibewatt import pricing

FIELDS = ("input", "cache_5m", "cache_1h", "cache_read", "output")


def drift(payload: dict, builtin: dict[str, pricing.Rate]) -> list[str]:
    report = []
    for model, remote in sorted(pricing._parse_remote(payload).items()):
        if not model.startswith("claude-"):
            continue
        local = builtin.get(model)
        if local is None:
            report.append(
                f"{model}: missing from BUILTIN (community input ${remote.input:g}/MTok)"
            )
            continue
        for field in FIELDS:
            ours, theirs = getattr(local, field), getattr(remote, field)
            if abs(ours - theirs) > 1e-9:
                report.append(
                    f"{model}: {field} built-in ${ours:g} vs community ${theirs:g}"
                )
    return report


def main() -> int:
    with urllib.request.urlopen(pricing.LITELLM_URL, timeout=30) as resp:
        raw = resp.read(pricing.REMOTE_MAX_BYTES + 1)
    if len(raw) > pricing.REMOTE_MAX_BYTES:
        print("community table exceeds the size cap")
        return 2
    report = drift(json.loads(raw), pricing.BUILTIN)
    print("\n".join(report) if report else "no drift")
    return 1 if report else 0


if __name__ == "__main__":
    raise SystemExit(main())
