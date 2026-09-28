# arkitekt-spec

The wire format of an Arkitekt app: one definition of *what an app is*, shared by everything
that produces or consumes it.

| Model | What it is | Produced by | Consumed by |
| --- | --- | --- | --- |
| `AppManifest`, `Requirement` | who the app is, which services it needs | arkitekt (from the `App`) | kabinet |
| `Inspection` | what the app declares (rekuest's action language, carried as JSON) | `arkitekt inspect all`, inside the image | arkitekt's plugin CLI, kabinet |
| `Selector` (`cpu`, `ram`, `cuda`, `rocm`, `oneapi`, `label`) | where a flavour may run | a flavour's `config.yaml` | kabinet, deployers |
| `DeploymentsFile` | `.arkitekt/deployments.yaml`, the images a repo publishes | `arkitekt plugin publish` | kabinet's repo scan |

It depends only on pydantic and pyyaml. It deliberately does **not** depend on rekuest: the
implementations, states, locks and bloks inside an inspection are rekuest's language, and each
side validates them with its own rekuest models.

## Compatibility

Every envelope model **ignores** unknown keys. An inspection is written by the arkitekt inside
an image and read by the host CLI, and `deployments.yaml` is written by the CLI and read by a
kabinet server that upgrades on its own schedule. Adding a field is therefore a minor change
an older reader survives. A change an older reader must refuse bumps `spec_version`.

Selectors are the exception: they refuse unknown keys, because they are written by hand and a
typo must not silently widen a placement constraint.

```python
from arkitekt_spec import load_deployments, dump_deployments

file = load_deployments(open(".arkitekt/deployments.yaml").read())
```

`python -m arkitekt_spec` prints the JSON Schema. The committed snapshot in
`tests/fixtures/deployments.schema.json` makes every format change show up in review.
