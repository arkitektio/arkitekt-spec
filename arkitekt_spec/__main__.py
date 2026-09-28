"""Print the JSON Schema of ``deployments.yaml``: ``python -m arkitekt_spec``."""

import json

from arkitekt_spec import json_schema

print(json.dumps(json_schema(), indent=2, sort_keys=True))
