from __future__ import annotations
import ccburn.pricing as p

def test_builtin_outranks_remote(monkeypatch):
    builtin = p.rate_for("claude-opus-5")
    assert builtin is not None
    fake = p.Rate(999.0, 999.0, 999.0, 999.0, 999.0)
    monkeypatch.setattr(p, "_remote", {"claude-opus-5": fake, "claude-zzz-9": fake})
    assert p.rate_for("claude-opus-5") == builtin
    assert p.rate_for("claude-zzz-9") == fake  # remote still fills gaps
