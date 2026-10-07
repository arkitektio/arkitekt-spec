"""Print a JSON Schema: ``python -m arkitekt_spec`` (deployments.yaml) or ``... release``."""

import json
import sys

from arkitekt_spec import json_schema, release_json_schema

schema = release_json_schema() if sys.argv[1:] == ["release"] else json_schema()
print(json.dumps(schema, indent=2, sort_keys=True))
