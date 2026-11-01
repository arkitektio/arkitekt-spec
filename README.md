# arkitekt-spec

The wire format of an Arkitekt app: one definition of *what an app is*, shared by everything
that produces or consumes it.

| Model | What it is | Produced by | Consumed by |
| --- | --- | --- | --- |
| `AppManifest`, `Requirement` | who the app is, which services it needs | arkitekt (from the `App`) | kabinet |
| `Inspection` | what the app declares (rekuest's action language, carried as JSON) | `arkitekt inspect all`, inside the image | arkitekt's plugin CLI, kabinet |
| `Selector` (`cpu`, `ram`, `cuda`, `rocm`, `oneapi`, `label`) | where a flavour may run | a flavour's `config.yaml` | kabinet, deployers |
| `Capability` (`container_engine`, `device`, `folder`, `host_network`, `linux_capability`, `shared_memory`) | what a flavour must be granted where it runs | a flavour's `config.yaml` | deployers, konstruktor |
| `DeploymentsFile` | `.arkitekt/deployments.yaml`, the images a repo publishes | `arkitekt plugin publish` | kabinet's repo scan |

It also owns the **action language** (`arkitekt_spec.actions`: definitions, ports,
implementations) and the **declaration layer** (`arkitekt_spec.declare`): `AppRegistry`, the
`@app.action` / `@app.workflow` / `@app.declare` machinery, structures, and the `Task` protocol a
runtime implements. SDKs build on it; apps import the same names from
[arkitekt](https://github.com/arkitektio/arkitekt). It depends on pydantic, pyyaml and three small
pure-python helpers, and on no runtime, transport or client.

## Workflows and recovery

An implementation carries two claims the server keeps:

| Field | Values | Meaning |
| --- | --- | --- |
| `execution` | `PLAIN`, `WORKFLOW` | Only a `WORKFLOW` may call other actions; it is resumed when its agent dies, a `PLAIN` task ends LOST. |
| `effects` | `NONE`, `REPEATABLE`, `UNKNOWN`, `IRREVERSIBLE` | What running it again would do. Information for whoever decides about a lost task, never a rule. |

`code_hash` pins a resume to the code that started the run. The errors a caller sees
(`AgentLost`, `NonDeterministicWorkflow`, `NotAWorkflowError`, `StateChanged`) live in
`arkitekt_spec.declare.errors`, and the workflow helpers (`record`, `retry`, `hold`, `guard`) are
on the `Task` protocol.

## Compatibility

Every envelope model **ignores** unknown keys. An inspection is written by the arkitekt inside
an image and read by the host CLI, and `deployments.yaml` is written by the CLI and read by a
kabinet server that upgrades on its own schedule. Adding a field is therefore a minor change
an older reader survives.

Every dumped file names the arkitekt-spec that wrote it in `spec_version` (`"7.1.0"`): a release
descriptor, `deployments.yaml`, `builds.yaml`, and each inspection, whose version is the image's
and may be older than the file's. A reader picks its parser by it, as kabinet does with its
`RELEASE_PARSERS`. The number `1`, or no key at all, marks a file older than this.

Selectors and capabilities are the exception: they refuse unknown keys, because they are
written by hand and a typo must not silently widen a placement constraint or change what is
granted.

## Capabilities

A selector says where a flavour may be *placed*; a capability says what it must be *granted*
once it is: the container engine's socket, a device, a folder of the host, the host's network,
a Linux capability, more shared memory. A capability names the container side only (`target`):
the host side differs per machine and is the installer's to find, or to ask the person
installing the app, who agrees to every capability first.

```yaml
# a flavour's config.yaml
capabilities:
  - kind: container_engine
    name: engine
    description: Starts and stops the apps the organization installs
  - kind: device
    name: camera
    class: video
    target: /dev/video0
  - kind: folder
    name: data
    target: /data
    readOnly: false
    required: false
```

A GPU is a selector (`cuda`, `rocm`, `oneapi`), not a capability: a flavour placed on one is
given it. There is no `privileged`, and `SYS_ADMIN`/`ALL` are refused: name what the app needs.
A flavour without capabilities is written as one was before they existed, so the digest of an
older descriptor does not change. An older reader ignores the field and places the flavour
without granting anything, which the app notices at start.

```python
from arkitekt_spec import load_deployments, dump_deployments

file = load_deployments(open(".arkitekt/deployments.yaml").read())
```

`python -m arkitekt_spec` prints the JSON Schema. The committed snapshot in
`tests/fixtures/deployments.schema.json` makes every format change show up in review.
