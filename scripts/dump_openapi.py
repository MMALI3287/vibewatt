"""Print the API's OpenAPI document so the frontend can generate its client types."""

from __future__ import annotations

import json

from vibewatt.api import create_app

print(json.dumps(create_app({"offline": True, "quota": False}).openapi(), indent=2))
