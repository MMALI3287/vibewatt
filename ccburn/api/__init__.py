"""FastAPI backend. `create_app()` is the entry point; `ccburn serve` mounts it."""

from __future__ import annotations

from .app import create_app

__all__ = ["create_app"]
