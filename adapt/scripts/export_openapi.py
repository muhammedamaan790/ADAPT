"""Write the API's OpenAPI document to docs/openapi.json (no server needed).

  uv run python scripts/export_openapi.py
The frontend can generate types from it (`openapi-typescript docs/openapi.json -o src/api/generated.ts`) or from a
running server (`npm run types:generate`)."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend")]

from adapt.api.main import create_app  # noqa: E402

out = ROOT / "docs" / "openapi.json"
out.write_text(json.dumps(create_app().openapi(), indent=1, sort_keys=True), encoding="utf-8")
print(f"wrote {out} ({len(create_app().openapi()['paths'])} paths)")
