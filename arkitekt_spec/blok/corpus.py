"""The parity corpus of the blok language: what the spec makes of v2 sources.

Three parsers implement v2 -- this package, takt (Rust) and orkestrator (TypeScript). The
corpus is how they agree: every case is a source with the tree the spec parses it into, or the
code it is refused with, or (for checks) the codes the validator finds. Parity compares trees
and codes, never message text.

::

    python -m arkitekt_spec.blok.corpus OUT_DIR    # writes bsx_v2.json

The file is self-describing: it carries the vocab version and the catalogs the spec checked
with. Parsing needs no catalog: text is stored as nodes. Every checked case is a ``<Blok>``
that declares what it owns (``<local>``, ``<query>``, ``<subscription>``) and names the catalog
it is written for; its ``mount`` is where it is checked: ``kinds`` (the kinds the mount takes,
empty for any), ``dependencies`` (null: mounted nowhere, every agent taken as written) and
``args``, and ``catalog``: whether it is checked against a catalog or held to the language
alone. A checked case's ``resolved`` is each ``<local>``'s port with its kind resolved, by node
id: what a checker checks against. ``language`` is the same with no catalog read at all
(``resolved`` and ``codes``): what a server, which holds a blok to the language alone, finds.
Dependencies are never inferred.
"""

import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from arkitekt_spec.actions import AgentDependencyInput, ComponentNodeInput, PortMatchInput
from arkitekt_spec.blok.bsx import parse_bsx
from arkitekt_spec.blok.catalogs import BlokView, blok_view, default_view
from arkitekt_spec.blok.declared import blok_catalog
from arkitekt_spec.blok.unparse import to_bsx
from arkitekt_spec.blok.vocabulary import JSON, BlokError, load_vocabulary

if TYPE_CHECKING:
    from arkitekt_spec.declare.blok.validate import BlokScope

# --------------------------------------------------------------------------- v2: parsed


VALID: Sequence[tuple[str, str]] = (
    ("an element", "<Card />"),
    ("a custom blok's agents", '<Blok kind="custom" context="panel">\n  <local name="to" like="agents.robot.move_to.position" />\n  <dependencies key="cells" like="agents.robot.move_to" />\n  <Text>{agents.robot.arm.mode}</Text>\n  <Button onClick={agents.robot.move_to(position: to) with cells into run} />\n</Blok>'),
    ("an explicit id", '<Card id="ot2-monitor"><Button /></Card>'),
    ("ids by name and occurrence", "<Card>\n  <Button />\n  <Text />\n  <Button />\n</Card>"),
    ("a literal is a literal", '<Card className="p-4"><Text text="@fff" /></Card>'),
    ("declared dependencies", '<Blok kind="agent" context="panel"><dependencies key="cells" like="self.analyze" /><Text>{cells.microscope.stage.position}</Text><Button onClick={cells.microscope.jog(dx: 1) into moved}>Right</Button></Blok>'),
    ("a call with its dependencies", '<Blok kind="agent" context="panel"><dependencies key="cells" like="self.analyze" /><Button onClick={self.analyze(tiles: 3) with cells}>Go</Button><Button onClick={self.analyze() with cells into run}>Again</Button></Blok>'),
    ("the options of a dependency, and a pick", '<Blok kind="agent" context="panel"><dependencies key="cells" like="self.analyze" /><foreach items={cells.options.microscope} let="o" key={o.id}><Button variant={o.picked ? "default" : "outline"} onClick={cells.pick(microscope: o.id)}>{o.name}</Button></foreach></Blok>'),
    ("a name that is no reference reads as written", "<Card><Text>{cells.microscope.stage}</Text></Card>"),
    ("a structure identifier", '<StructurePicker identifier="@mikro/arraydataset" />'),
    ("a bare attribute is true", '<Button disabled />'),
    ("entities", '<Text text="&lt;b&gt; &amp; &#x41; &quot;q&quot;" />'),
    ("a path", "<Text text={deps.robot.arm.mode} />"),
    ("self", "<Text text={self.arm.joints} />"),
    ("a latest task", "<Progress value={self.tasks.move.progress} />"),
    ("an asset", "<Scene><Model src={self.assets.scope} /></Scene>"),
    ("literals", '<Chart data={[1, 2.5, -3, true, false, null, "x", {a: 1, "b-c": [2]}]} />'),
    ("numbers", "<Slider min={0} max={1e3} step={0.25} />"),
    ("string escapes", "<Text text={'it\\'s \"q\" \\n \\u00e9'} />"),
    ("a function, positional", "<Text text={str.upper(deps.robot.name)} />"),
    ("a function, named", "<Text text={str.truncate(value: task.message, length: 20)} />"),
    ("a function's optional argument left out", "<Text text={list.join(args.tags)} />"),
    ("variadic values", "<Text text={math.max(1, 2, deps.robot.x)} />"),
    ("comparisons", "<Badge when={deps.robot.temperature >= 34} />"),
    ("equality", '<Badge when={task.status == "done"} />'),
    ("and, or, not", "<Badge when={task.running && !task.failed || deps.robot.busy} />"),
    ("a flat and", "<Badge when={a && b && c} />"),
    ("parentheses keep a group", "<Badge when={(a && b) && c} />"),
    ("a ternary", '<Button variant={move.args.position == p.id ? "default" : "outline"} />'),
    ("a nested ternary", '<Badge text={a ? "x" : b ? "y" : "z"} />'),
    ("a task call", "<Button onClick={self.deconvolve(image: args.image, iterations: iterations)} />"),
    ("a task call into a record", "<Button onClick={deps.robot.move_to(position: 3) into move} />"),
    ("into inside a foreach", '<foreach items={self.positions} let="p" key={p.id}><Button onClick={self.move(to: p.id) into move} /></foreach>'),
    ("event in a callback", "<Slider onChange={deps.stage.set(x: event.value)} />"),
    ("a refetch", "<Button onClick={queries.positions.refetch()} />"),
    ("a fetch_more", "<Button onClick={queries.positions.fetch_more(after: queries.positions.value.cursor, first: 20)} />"),
    ("a fetch_more with no variables", "<Button onClick={queries.positions.fetch_more()} />"),
    ("query.refetch written as the call", '<Button onClick={query.refetch("positions")} />'),
    ("query.fetch_more written as the call", '<Button onClick={query.fetch_more(query: "positions", variables: {page: 2})} />'),
    ("then", "<Text text={then} />"),
    ("a time", '<Card><Time value={self.arm.since} relative /><Time value={then} format="HH:mm" /></Card>'),
    ("ui.copy", "<Button onClick={ui.copy(task.id)} />"),
    ("task.answer", '<Button onClick={task.answer(values: {image: args.image})} />'),
    ("bind a local", "<Slider bind={iterations} />"),
    ("bind a field of a local", "<Input bind={form.name} />"),
    ("text fills the text prop", "<Button>Grip</Button>"),
    ("a path as text", "<Badge>{task.status}</Badge>"),
    ("text and a path", "<Text>Arrived at {move.args.position}</Text>"),
    ("text with braces", "<Text>a {\"{\"} b</Text>"),
    ("jsx whitespace", "<Text>\n    Arrived   at\n    {move.args.position}\n  </Text>"),
    ("a forced space", '<Text>{a}{" "}{b}</Text>'),
    ("mixed text and elements", "<p>Hello <span>world</span> and {viewer.user.username}</p>"),
    ("text in a language component", "<if when={x}>Yes<else>No</else></if>"),
    ("loose text in html is a span", "<div><if when={x}>Hi <p>there</p></if></div>"),
    ("loose text in ui is a Text", "<Alert>Failed <Badge>{task.status}</Badge></Alert>"),
    ("if, else-if, else", "<if when={run.failed}><Alert>Failed</Alert><else when={run.done}><Text>Done</Text></else><else><Spinner /></else></if>"),
    ("a foreach", '<foreach items={deps.robot.positions} let="p" key={p.id}><Text>{p.name}</Text></foreach>'),
    ("match", '<match value={task.status}><case is="done"><Badge>ok</Badge></case><default><Spinner /></default></match>'),
    ("suspense", "<suspense><fallback><Skeleton /></fallback><error><Alert>Could not load: {error.message}</Alert></error><Badge>{deps.robot.arm.mode}</Badge></suspense>"),
    ("a guard", "<Alert when={move.failed}>Move failed: {move.error}</Alert>"),
    ("clsx list", '<Badge className={["px-2", task.running && "animate-pulse", task.failed && "bg-destructive/15"]} />'),
    ("clsx object", '<Badge className={{"animate-pulse": task.running, ring: !task.done}} />'),
    ("a toast", "<Toast task={move}><done>Arrived at {move.args.position}</done><failed>Move failed: {move.error}</failed></Toast>"),
    ("a component's own persist prop", '<Scene id="main" camera="front" persist><Model src={self.assets.arm} /></Scene>'),
    ("a space", '<Blok kind="action" context="scene"><Space room="room:lab"><Placed dep="robot"><Glow on={task.running} /></Placed><Route through={["robot", "ot2"]} /></Space></Blok>'),
    ("a logo", '<Blok kind="agent" context="logo"><Logo viewBox="0 0 64 64"><G fill="ink"><Rect x="0" y="0" width="64" height="64" /></G></Logo></Blok>'),
    ("a blok", '<Blok kind="action" context="panel" chrome="none" catalog="https://arkitekt.live/catalogs/orkestrator/v1"><Card /></Blok>'),
    ("a local declares its name", '<Blok kind="agent" context="panel"><local name="iterations" default={15} persist /><Slider bind={iterations} /></Blok>'),
    ("a local with a port", '<Blok kind="agent" context="panel"><local name="mode" kind="ENUM" choices={[{value: "a", label: "A"}]} nullable /><Text>{mode}</Text></Blok>'),
    ("a local borrowing an argument", '<Blok kind="agent" context="panel"><local name="to" like="self.move.to" /><Button onClick={self.move(to: to)} /></Blok>'),
    ("a local in a loop", '<foreach items={args.xs} let="x" key={x.id}><local name="open" default={false} /><Switch bind={open} /></foreach>'),
    ("a query and its variables", '<Blok kind="agent" context="panel"><query name="positions" service="mikro" document="query ($slide: ID!) { positions(slide: $slide) { id } }" poll={5}><var key="slide" value={args.slide.id} /><var key="limit" value={20} /></query><Text>{queries.positions.status}</Text></Blok>'),
    ("a subscription", '<Blok kind="agent" context="panel"><subscription name="events" service="mikro" document="subscription { events { id } }" keep={10} /><Text>{subscriptions.events.status}</Text></Blok>'),
    ("comments", "<Card><!-- a comment --><Button />{/* another */}</Card>"),
    ("a multi-line attribute", '<Card>\n  <Button\n    label="multi\n line"\n  />\n</Card>'),
    ("a trailing comma", "<Text text={str.concat([a, b,])} />"),
    ("a null argument", "<Button onClick={self.go(to: null)} />"),
    ("a foreach without key", '<foreach items={self.names} let="n"><Text>{n}</Text></foreach>'),
    ("a foreach with an index", '<foreach items={self.names} let="n" index="i"><Text>{math.add(a: i, b: 1)}</Text></foreach>'),
    ("coalesce", "<Text text={a ?? b} />"),
    ("a coalesce chain is one call", "<Text text={a ?? b ?? c} />"),
    ("parentheses keep a coalesce group", "<Text text={(a ?? b) ?? c} />"),
    ("coalesce binds looser than or", "<Text text={a || b ?? c} />"),
    ("coalesce binds tighter than a ternary", '<Badge text={a ?? b ? "x" : "y"} />'),
    ("coalesce inside and", "<Badge when={(a ?? b) && c} />"),
    ("logic.coalesce written as the call", "<Text text={logic.coalesce(a, b)} />"),
    ("spectral pens", '<Blok kind="agent" context="logo"><Logo><Path d="M0 0" fill="spectrum-red" stroke="spectrum-blue" /></Logo></Blok>'),
)

REFUSED: Sequence[tuple[str, str, str]] = (
    ("empty", "", "bsx.empty"),
    ("two roots", "<A/><B/>", "bsx.multiple_roots"),
    ("unclosed", "<Card>\n  <Button>\n", "bsx.unclosed"),
    ("mismatched", "<Card><Button></Card>", "bsx.mismatched_tag"),
    ("an attribute twice", '<Card id="a" id="b" />', "bsx.duplicate_attribute"),
    ("an id twice", '<Card><Button id="go" /><Button id="go" /></Card>', "bsx.duplicate_id"),
    ("an id that is no name", '<Card id="a/b" />', "bsx.id"),
    ("an id from an expression", "<Card id={x} />", "bsx.id"),
    ("a call in text", "<Text>{self.run()}</Text>", "bsx.text_callback"),
    ("else outside if", "<Card><else /></Card>", "bsx.else_outside_if"),
    ("then after else", "<if when={x}><else><A /></else><B /></if>", "bsx.else_order"),
    ("a bare else before another", "<if when={x}><else><A /></else><else><B /></else></if>", "bsx.else_order"),
    ("a foreach without items", '<foreach let="p" key={p}><Text /></foreach>', "bsx.foreach"),
    ("a foreach with a literal key", '<foreach items={xs} let="p" key="id"><Text /></foreach>', "bsx.foreach"),
    ("a foreach with a literal items", '<foreach items="xs" let="p" key={p}><Text /></foreach>', "bsx.foreach"),
    ("a sigil in a let", '<foreach items={xs} let="#p" key={p}><Text /></foreach>', "bsx.let"),
    ("an index from an expression", '<foreach items={xs} let="p" index={i}><Text /></foreach>', "bsx.index"),
    ("a local named by an expression", "<local name={x} />", "local.attribute"),
    ("a local named no name", '<local name="a.b" />', "local.attribute"),
    ("a sequence", "<Button onClick={self.a(); self.b()} />", "callback.sequence"),
    ("an assignment", '<Button onClick={camera = "iso"} />', "callback.assignment"),
    ("await", "<Button onClick={await self.a()} />", "callback.await"),
    ("a positional task argument", "<Button onClick={self.go(1)} />", "callback.positional"),
    ("a task call inside an expression", "<Text text={str.upper(self.go(x: 1))} />", "callback.nested"),
    ("an effect inside an expression", "<Text text={str.upper(ui.copy(x))} />", "callback.nested"),
    ("into a path", "<Button onClick={task.status into run} />", "callback.into"),
    ("with on a path", "<Button onClick={task.status with cells} />", "callback.with"),
    ("with on a function", "<Button onClick={str.upper(value: x) with cells} />", "callback.with"),
    ("deps in a custom blok", '<Blok kind="custom" context="panel"><Text text={deps.robot.arm.mode} /></Blok>', "roots.custom_deps"),
    ("a like through deps in a custom blok", '<Blok kind="custom" context="panel"><dependencies key="cells" like="deps.robot.analyze" /><Card /></Blok>', "roots.custom_deps"),
    ("with no key", "<Button onClick={self.run() with} />", "callback.with"),
    ("with an object", "<Button onClick={self.run() with {a: deps.b}} />", "callback.with"),
    ("a pick of nothing", '<Blok kind="agent" context="panel"><dependencies key="cells" like="self.analyze" /><Button onClick={cells.pick()} /></Blok>', "expr.arguments"),
    ("into on a function", "<Button onClick={str.upper(x) into run} />", "callback.into"),
    ("arithmetic", "<Text text={a + 1} />", "expr.arithmetic"),
    ("negating a path", "<Text text={-a} />", "expr.arithmetic"),
    ("a subscript", "<Text text={a[0]} />", "expr.subscript"),
    ("a chained comparison", "<Badge when={a < b < c} />", "expr.chained_comparison"),
    ("a lambda", "<Text text={xs => x} />", "expr.lambda"),
    ("a list holding expressions", "<Chart data={[a, 1]} />", "expr.dynamic_container"),
    ("an unknown callee", "<Button onClick={go()} />", "expr.unknown_callee"),
    ("a utils. call", "<Text text={utils.gt(a, 1)} />", "expr.unknown_callee"),
    ("an actions. call", "<Button onClick={actions.robot.go()} />", "expr.unknown_callee"),
    ("too many arguments", "<Badge when={cmp.gt(1, 2, 3)} />", "expr.arguments"),
    ("an unknown argument", "<Badge when={cmp.gt(1, c: 2)} />", "expr.arguments"),
    ("a missing argument", "<Badge when={cmp.gt(1)} />", "expr.arguments"),
    ("an argument given twice", "<Badge when={cmp.gt(1, a: 2)} />", "expr.arguments"),
    ("a key given twice", "<Chart data={{a: 1, a: 2}} />", "expr.duplicate_key"),
    ("an unterminated string", "<Text text={'abc} />", "bsx.syntax"),
    ("a string not closed on its line", "<Text text={str.upper('abc\n)} />", "expr.syntax"),
    ("strict equality", "<Badge when={a === b} />", "expr.syntax"),
    ("an empty expression", "<Text text={} />", "expr.syntax"),
    ("a call's field", "<Text text={str.upper(a).length} />", "expr.syntax"),
    ("a subscription refetch", "<Button onClick={subscriptions.events.refetch()} />", "subscription.refetch"),
    ("a subscription fetch_more", "<Button onClick={subscriptions.events.fetch_more(page: 2)} />", "subscription.refetch"),
    ("a refetch with arguments", "<Button onClick={queries.positions.refetch(page: 2)} />", "expr.arguments"),
    ("a positional fetch_more variable", "<Button onClick={queries.positions.fetch_more(2)} />", "callback.positional"),
    ("a fetch_more variable given twice", "<Button onClick={queries.positions.fetch_more(page: 1, page: 2)} />", "expr.duplicate_key"),
    ("a fetch_more inside an expression", "<Text text={str.upper(queries.positions.fetch_more())} />", "callback.nested"),
    ("a refetch in text", "<Text>{queries.positions.refetch()}</Text>", "bsx.text_callback"),
    ("a query effect inside an expression", '<Text text={str.upper(query.refetch("positions"))} />', "callback.nested"),
    ("a stray brace in text", "<Text>a } b</Text>", "bsx.syntax"),
    ("optional chaining", "<Text text={a?.b} />", "expr.syntax"),
    ("a dangling coalesce", "<Text text={a ??} />", "expr.syntax"),
)


# --------------------------------------------------------------------------- v2: checked


@dataclass(frozen=True)
class Check:
    """A blok checked where it is mounted: the codes the validator finds, in order."""

    name: str
    source: str
    codes: tuple[str, ...]
    dependencies: tuple[Mapping[str, JSON], ...] | None = ()
    """The mount's dependencies; None: mounted nowhere, every agent taken as written."""
    args: tuple[str, ...] | None = None
    kinds: tuple[str, ...] = ()
    """The kinds the mount takes; empty: any."""
    catalog: bool = True
    """Whether it is checked against a catalog at all, or held to the language alone."""
    port: Mapping[str, JSON] | None = None
    """The port a port blok is mounted on, as a match: what ``port.value`` is typed as."""
    workflows: Mapping[str, tuple[Mapping[str, JSON], ...]] | None = None
    """Per action of the blok's own agent, that action's dependencies: what an agent blok's
    ``<dependency>`` borrows from; None: not known where it is checked."""


_ROBOT: Mapping[str, JSON] = {
    "key": "robot",
    "action_dependencies": [{"key": "move_to", "demand": {"arg_matches": [{"key": "position"}], "return_matches": [{"key": "arrived"}]}}],
    "state_dependencies": [{"key": "arm", "demand": {"matches": [{"key": "joints", "kind": "LIST"}, {"key": "mode", "kind": "STRING"}]}}],
}
_SELF: Mapping[str, JSON] = {**_ROBOT, "key": "self"}
_TYPED_SELF: Mapping[str, JSON] = {
    "key": "self",
    "action_dependencies": [
        {"key": "move_to", "demand": {"arg_matches": [{"key": "position", "kind": "INT"}, {"key": "speed", "kind": "FLOAT"}, {"key": "image", "kind": "STRUCTURE", "identifier": "@mikro/image"}]}},
    ],
}
_POSITIONS: Mapping[str, JSON] = {"key": "positions", "service": "mikro", "document": "query { positions { id } }"}
_PAGED: Mapping[str, JSON] = {**_POSITIONS, "more": "positions"}
_ITERATIONS: Mapping[str, JSON] = {"key": "iterations", "kind": "INT", "default": 15}
_NAME: Mapping[str, JSON] = {"key": "name", "kind": "STRING", "default": ""}
_SHELF: Mapping[str, JSON] = {
    "key": "shelf",
    "state_dependencies": [{"key": "inventory", "demand": {"matches": [
        {"key": "slots", "kind": "LIST", "children": [{"key": "...", "kind": "DICT", "children": [{"key": "id", "kind": "STRING"}]}]},
        {"key": "names", "kind": "LIST", "children": [{"key": "...", "kind": "STRING"}]},
    ]}}],
}
_CAMERA: Mapping[str, JSON] = {
    "key": "self",
    "state_dependencies": [{"key": "camera", "demand": {"matches": [
        {"key": "streams", "kind": "LIST", "children": [{"key": "...", "kind": "STRUCTURE", "identifier": "@lovekit/stream"}]},
    ]}}],
}
_UNNAMED: Mapping[str, JSON] = {
    "key": "self",
    "action_dependencies": [{"key": "measure", "demand": {"return_matches": [{"key": "return0", "kind": "FLOAT"}]}}],
}
_FORM: Mapping[str, JSON] = {"key": "form", "kind": "DICT", "children": [{"key": "name", "kind": "STRING"}, {"key": "count", "kind": "INT"}]}

_TO: Mapping[str, JSON] = {
    "key": "self",
    "action_dependencies": [{"key": "move", "demand": {"arg_matches": [{"key": "to", "kind": "INT"}]}}],
}
_MICROSCOPE: Mapping[str, JSON] = {
    "key": "microscope",
    "app": "openuc",
    "action_dependencies": [{"key": "jog", "demand": {"arg_matches": [{"key": "dx", "kind": "FLOAT"}]}}],
    "state_dependencies": [{"key": "stage", "demand": {"matches": [{"key": "position", "kind": "STRING"}]}}],
}
_CAMERA_ONLY: Mapping[str, JSON] = {"key": "camera", "app": "openuc", "action_dependencies": [{"key": "snap", "demand": {}}]}
_OTHER_APP: Mapping[str, JSON] = {**_MICROSCOPE, "key": "stage", "app": "thorlabs"}
_WORKFLOWS: Mapping[str, tuple[Mapping[str, JSON], ...]] = {
    "analyze": (_MICROSCOPE, _CAMERA_ONLY),
    "scan": (_OTHER_APP,),
    "plain": (),
    "awkward": ({**_MICROSCOPE, "key": "options"},),
}
_CELLS = '<Blok kind="agent" context="panel"><dependencies key="cells" like="self.analyze" />'
_PANEL = '<Blok kind="agent" context="panel">'
_PORT = '<Blok kind="port" context="panel">'
_EXPOSURE: Mapping[str, JSON] = {"key": "exposure", "kind": "FLOAT", "nullable": False}
_REGION: Mapping[str, JSON] = {"key": "region", "kind": "MODEL", "children": [{"key": "x", "kind": "INT"}, {"key": "y", "kind": "INT"}]}

CHECKS: Sequence[Check] = (
    Check('an agent blok reads, calls and hands over the dependencies it declares', f'{_CELLS}<Card><Text>{{cells.microscope.stage.position}}</Text><Text>{{cells.microscope.connected ? "on" : "off"}}</Text><Button onClick={{cells.microscope.jog(dx: 100) into moved}}>Right</Button><Button onClick={{cells.camera.snap()}}>Snap</Button><Button onClick={{self.analyze() with cells into run}}>Analyze</Button></Card></Blok>', (), workflows=_WORKFLOWS),
    Check('custom pickers of declared dependencies', f'{_CELLS}<Card><foreach items={{cells.options.microscope}} let="o" key={{o.id}}><Button disabled={{!o.connected}} variant={{o.picked ? "default" : "outline"}} onClick={{cells.pick(microscope: o.id)}}>{{o.name}}</Button></foreach><deppicker key="cells.camera" /><deppicker key="cells" /></Card></Blok>', (), workflows=_WORKFLOWS),
    Check('declared dependencies, their action not known here', f'{_CELLS}<Card><Text>{{cells.anything.at_all}}</Text><Text>{{list.len(value: cells.options.whatever)}}</Text><Button onClick={{cells.pick(whatever: "1")}} /><Button onClick={{self.analyze() with cells}} /><deppicker key="cells.whatever" /></Card></Blok>', (), dependencies=None),
    Check('declared dependencies are held to their action', f'{_CELLS}<Card><Text>{{cells.microscope.camera.live}}</Text><Button onClick={{cells.microscope.fly()}} /><Text>{{cells.laser.power}}</Text><Button onClick={{cells.laser.fire()}} /></Card></Blok>', ("deps.unknown_state", "deps.unknown_action", "deps.unknown", "deps.unknown"), workflows=_WORKFLOWS),
    Check('an option has four fields', f'{_CELLS}<foreach items={{cells.options.microscope}} let="o" key={{o.id}}><Text>{{o.stage}}</Text></foreach></Blok>', ("path.unknown",), workflows=_WORKFLOWS),
    Check('options and a pick of no dependency', f'{_CELLS}<Card><Text>{{list.len(value: cells.options.laser)}}</Text><Button onClick={{cells.pick(laser: "1")}} /><deppicker key="cells.laser" /><deppicker key="other" /></Card></Blok>', ("deps.unknown", "deps.unknown", "deps.unknown", "deps.unknown"), workflows=_WORKFLOWS),
    Check('dependencies like no action of the app', '<Blok kind="agent" context="panel"><dependencies key="cells" like="self.nothing" /><Text>{cells.microscope.stage.position}</Text></Blok>', ("dependencies.like",), workflows=_WORKFLOWS),
    Check('dependencies like an action with none', '<Blok kind="agent" context="panel"><dependencies key="cells" like="self.plain" /><Card /></Blok>', ("dependencies.like",), workflows=_WORKFLOWS),
    Check('dependencies like an action with one called options', '<Blok kind="agent" context="panel"><dependencies key="cells" like="self.awkward" /><Card /></Blok>', ("dependencies.like",), workflows=_WORKFLOWS),
    Check('dependencies like something else', '<Blok kind="agent" context="panel"><dependencies key="cells" like="self.analyze.microscope" /><Card /></Blok>', ("dependencies.like",), dependencies=None),
    Check('dependencies without a like', '<Blok kind="agent" context="panel"><dependencies key="cells" /><Card /></Blok>', ("dependencies.like",), dependencies=None, catalog=False),
    Check('dependencies declared twice', f'{_CELLS}<dependencies key="cells" like="self.scan" /><Card /></Blok>', ("dependencies.key",), workflows=_WORKFLOWS),
    Check('dependencies keyed self', '<Blok kind="agent" context="panel"><dependencies key="self" like="self.analyze" /><Card /></Blok>', ("dependencies.key",), workflows=_WORKFLOWS),
    Check('dependencies below the top', '<Blok kind="agent" context="panel"><Card><dependencies key="cells" like="self.analyze" /></Card></Blok>', ("dependencies.position",), workflows=_WORKFLOWS, catalog=False),
    Check('dependencies in an action blok', '<Blok kind="action" context="panel"><dependencies key="cells" like="self.analyze" /><Card /></Blok>', ("dependencies.position",), catalog=False),
    Check('with the dependencies of another action', f'{_CELLS}<Button onClick={{self.scan() with cells}} /></Blok>', ("with.mismatch",), workflows=_WORKFLOWS),
    Check('with dependencies the blok does not declare', f'{_CELLS}<Button onClick={{self.analyze() with other}} /></Blok>', ("deps.unknown",), workflows=_WORKFLOWS),
    Check('a clean agent blok', '<Blok kind="agent" context="panel"><Card><Text>{self.arm.mode}</Text><Button onClick={self.move_to(position: 1) into move}>Go</Button><Progress value={move.progress} /></Card></Blok>', (), dependencies=(_SELF,)),
    Check('an undeclared dependency in an agent blok', '<Blok kind="agent" context="panel"><Text text={deps.robot.arm.mode} /></Blok>', ("deps.unknown",), dependencies=(_ROBOT,)),
    Check('a custom blok declares the dependencies of a bound agent\'s action', '<Blok kind="custom" context="panel"><dependencies key="cells" like="agents.robot.move_to" /><Card><Text>{cells.microscope.stage.position}</Text><deppicker key="cells" /><Button onClick={agents.robot.move_to(position: 1) with cells into run} /><Button onClick={cells.microscope.jog(dx: 1)} /></Card></Blok>', (), dependencies=(_ROBOT,)),
    Check('dependencies like an agent a custom blok is not bound to', '<Blok kind="custom" context="panel"><dependencies key="cells" like="agents.stage.scan" /><Card /></Blok>', ("dependencies.like",), dependencies=(_ROBOT,)),
    Check('dependencies like self in a custom blok', '<Blok kind="custom" context="panel"><dependencies key="cells" like="self.analyze" /><Card /></Blok>', ("dependencies.like",), dependencies=(_ROBOT,)),
    Check('dependencies like a bound agent in an agent blok', '<Blok kind="agent" context="panel"><dependencies key="cells" like="deps.robot.analyze" /><Card /></Blok>', ("dependencies.like",), workflows=_WORKFLOWS),
    Check('with the dependencies of another action', '<Blok kind="custom" context="panel"><dependencies key="cells" like="agents.robot.scan" /><Button onClick={agents.robot.move_to(position: 1) with cells} /></Blok>', ("with.mismatch",), dependencies=(_ROBOT,)),
    Check('agents in an agent blok', '<Blok kind="agent" context="panel"><Text text={agents.robot.arm.mode} /></Blok>', ("roots.not_in_kind",)),
    Check('self in a custom blok', '<Blok kind="custom" context="panel"><Text text={self.arm.mode} /></Blok>', ("roots.not_in_kind",), dependencies=(_ROBOT,)),
    Check('task in an agent blok', '<Blok kind="agent" context="panel"><Text text={task.status} /></Blok>', ("roots.not_in_kind",)),
    Check('an action logo reads only task', '<Blok kind="action" context="logo"><Logo><Path d="M0 0" fill={task.running ? "brand" : "muted"} stroke={viewer.user.id} /></Logo></Blok>', ("roots.not_in_kind",)),
    Check('an unknown root', '<Blok kind="agent" context="panel"><Text text={nothing.here} /></Blok>', ("roots.unknown",)),
    Check('a function namespace is no value', '<Blok kind="agent" context="panel"><Text text={math.pi} /></Blok>', ("roots.unknown",)),
    Check('event outside a callback', '<Blok kind="agent" context="panel"><Text text={event.value} /></Blok>', ("roots.event_outside_callback",)),
    Check('error outside its slot', '<Blok kind="agent" context="panel"><Text text={error.message} /></Blok>', ("roots.error_outside_boundary",)),
    Check('a reserved local', '<Blok kind="agent" context="panel"><local name="task" kind="STRING" /><Text /></Blok>', ("roots.reserved",)),
    Check('then is reserved', '<Blok kind="agent" context="panel"><local name="then" kind="STRING" /><Text /></Blok>', ("roots.reserved",)),
    Check('query is reserved', '<Blok kind="agent" context="panel"><local name="query" kind="STRING" /><Text /></Blok>', ("roots.reserved",)),
    Check('a let shadowing a local', '<Blok kind="agent" context="panel"><local name="x" kind="STRING" /><foreach items={args.xs} let="x" key={x}><Text /></foreach></Blok>', ("roots.shadow",)),
    Check('an index is a number', '<Blok kind="agent" context="panel"><foreach items={args.xs} let="x" index="i" key={i}><Progress value={i} /><Text text={i.name} /></foreach></Blok>', ("path.unknown",)),
    Check('an index shadowing its let', '<Blok kind="agent" context="panel"><foreach items={args.xs} let="x" index="x" key={x}><Text /></foreach></Blok>', ("roots.shadow",)),
    Check('one record, two calls', '<Blok kind="agent" context="panel"><Card><Button onClick={self.move_to(position: 1) into move} /><Button onClick={self.move_to(position: 2) into move} /></Card></Blok>', ("into.twice",), dependencies=(_SELF,)),
    Check('an unknown dependency', '<Blok kind="custom" context="panel"><Text text={agents.nope.x} /></Blok>', ("deps.unknown",), dependencies=(_ROBOT,)),
    Check('a custom blok reading an undeclared dependency', '<Blok kind="custom" context="panel"><Card><Text text={agents.robot.arm.mode} /><Button onClick={agents.stage.move(x: 1)} /></Card></Blok>', ('deps.unknown', 'deps.unknown')),
    Check('an undeclared dependency beside a declared one', '<Blok kind="custom" context="panel"><Card><Text text={agents.robot.arm.mode} /><Text text={agents.camera.frame} /></Card></Blok>', ("deps.unknown",), dependencies=(_ROBOT,)),
    Check('a query variable reading an undeclared dependency', '<Blok kind="custom" context="panel"><query name="positions" service="mikro" document="query { positions { id } }"><var key="agent" value={agents.stage.id} /></query><Text text={queries.positions.status} /></Blok>', ("deps.unknown",)),
    Check('an unknown state', '<Blok kind="custom" context="panel"><Text text={agents.robot.legs} /></Blok>', ("deps.unknown_state",), dependencies=(_ROBOT,)),
    Check('an unknown action', '<Blok kind="custom" context="panel"><Button onClick={agents.robot.fly()} /></Blok>', ("deps.unknown_action",), dependencies=(_ROBOT,)),
    Check('an unknown field', '<Blok kind="custom" context="panel"><Text text={agents.robot.arm.speed} /></Blok>', ("path.unknown",), dependencies=(_ROBOT,)),
    Check('a task record field', '<Blok kind="custom" context="panel"><Text text={agents.robot.tasks.move_to.returns.arrived} /></Blok>', (), dependencies=(_ROBOT,)),
    Check('an unknown record field', '<Blok kind="agent" context="panel"><Card><Button onClick={self.move_to(position: 1) into move} /><Text text={move.result} /></Card></Blok>', ("path.unknown",), dependencies=(_SELF,)),
    Check('an undeclared arg', '<Blok kind="agent" context="panel"><Text text={args.image} /></Blok>', ("args.unknown",), args=('dataset',)),
    Check('an args read whole', '<Blok kind="agent" context="panel"><Text text={args} /></Blok>', ("roots.whole",)),
    Check('an undeclared query', '<Blok kind="agent" context="panel"><Text text={queries.cells.value} /></Blok>', ("query.unknown",)),
    Check('a declared query', '<Blok kind="agent" context="panel"><query name="positions" service="mikro" document="query { positions { id } }" /><Card><Text text={queries.positions.status} /><Button onClick={queries.positions.refetch()} /></Card></Blok>', ()),
    Check('refetch of an undeclared query', '<Blok kind="agent" context="panel"><Button onClick={queries.cells.refetch()} /></Blok>', ("query.unknown",)),
    Check('fetch_more of a paged query', '<Blok kind="agent" context="panel"><query name="positions" service="mikro" document="query { positions { id } }" more="positions" /><Button onClick={queries.positions.fetch_more(after: queries.positions.value.cursor)} /></Blok>', ()),
    Check('fetch_more of a query without more', '<Blok kind="agent" context="panel"><query name="positions" service="mikro" document="query { positions { id } }" /><Button onClick={queries.positions.fetch_more()} /></Blok>', ("query.fetch_more",)),
    Check('fetch_more of an undeclared query', '<Blok kind="agent" context="panel"><Button onClick={queries.cells.fetch_more()} /></Blok>', ("query.unknown",)),
    Check('a query effect named by a path', '<Blok kind="agent" context="panel"><Button onClick={query.refetch(args.which)} /></Blok>', ("query.effect_target",)),
    Check('fetching_more is a field of the record', '<Blok kind="agent" context="panel"><query name="positions" service="mikro" document="query { positions { id } }" more="positions" /><Spinner when={queries.positions.fetching_more} /></Blok>', ()),
    Check('then is the viewed moment', '<Blok kind="agent" context="panel"><Text text={then} /></Blok>', ()),
    Check('then has no fields', '<Blok kind="agent" context="panel"><Text text={then.year} /></Blok>', ("path.unknown",)),
    Check('a structure reads as its reference', '<Blok kind="agent" context="panel"><foreach items={self.camera.streams} let="stream" key={stream.object}><Text>{stream.__identifier}</Text></foreach></Blok>', (), dependencies=(_CAMERA,)),
    Check('a structure has no other field', '<Blok kind="agent" context="panel"><foreach items={self.camera.streams} let="stream" key={stream.id}><Text /></foreach></Blok>', ("path.unknown",), dependencies=(_CAMERA,)),
    Check('then in an action logo', '<Blok kind="action" context="logo"><Logo><Path d="M0 0" fill={then ? "muted" : "brand"} /></Logo></Blok>', ("roots.not_in_kind",)),
    Check('now is no root', '<Blok kind="agent" context="panel"><Text text={now} /></Blok>', ("roots.unknown",)),
    Check('time.since is gone', '<Blok kind="agent" context="panel"><Text text={time.since(task.message)} /></Blok>', ('expr.unknown_function', 'roots.not_in_kind')),
    Check('time.diff is pure', '<Blok kind="agent" context="panel"><Text text={fmt.duration(time.diff(args.start, args.end))} /></Blok>', ()),
    Check('a time component', '<Blok kind="agent" context="panel"><Time value={args.started} relative /></Blok>', ()),
    Check('a callback holding a value', '<Blok kind="agent" context="panel"><Button onClick={"x"} /></Blok>', ("callback.unbound",)),
    Check('a task call in a value prop', '<Blok kind="agent" context="panel"><Text text={self.move_to(position: 1)} /></Blok>', ("callback.not_callback",), dependencies=(_SELF,)),
    Check('an unknown event field', '<Blok kind="agent" context="panel"><Slider onChange={self.move_to(position: event.nope)} /></Blok>', ("callback.event_field",), dependencies=(_SELF,)),
    Check('bind to no local', '<Blok kind="custom" context="panel"><Slider bind={agents.robot.arm} /></Blok>', ("local.unknown",), dependencies=(_ROBOT,)),
    Check('a bind that is a literal', '<Blok kind="agent" context="panel"><local name="iterations" kind="INT" default={15} /><Slider bind="iterations" /></Blok>', ("local.bind",)),
    Check('a default that does not fit its kind', '<Blok kind="agent" context="panel"><local name="iterations" kind="INT" default={"15"} /><Slider bind={iterations} /></Blok>', ("local.default",)),
    Check('a persisted local nothing binds', '<Blok kind="agent" context="panel"><local name="iterations" kind="INT" default={15} persist /><Text /></Blok>', ("local.unused_persist",)),
    Check('a slider binds an int local', '<Blok kind="agent" context="panel"><local name="iterations" kind="INT" default={15} /><Slider bind={iterations} /></Blok>', ()),
    Check('an input binds a string local', '<Blok kind="agent" context="panel"><local name="name" kind="STRING" default={""} /><Input bind={name} /></Blok>', ()),
    Check('a switch bound to an int local', '<Blok kind="agent" context="panel"><local name="iterations" kind="INT" default={15} /><Switch bind={iterations} /></Blok>', ("local.bind_kind",)),
    Check('an input bound to an int local', '<Blok kind="agent" context="panel"><local name="iterations" kind="INT" default={15} /><Input bind={iterations} /></Blok>', ("local.bind_kind",)),
    Check('a slider bound to a string local', '<Blok kind="agent" context="panel"><local name="name" kind="STRING" default={""} /><Slider bind={name} /></Blok>', ("local.bind_kind",)),
    Check('a bind checks the field it writes', '<Blok kind="agent" context="panel"><local name="form" kind="DICT" children={[{"key": "name", "kind": "STRING"}, {"key": "count", "kind": "INT"}]} /><Card><Input bind={form.name} /><Switch bind={form.count} /></Card></Blok>', ("local.bind_kind",)),
    Check('an ANY control binds any local', '<Blok kind="agent" context="panel"><local name="iterations" kind="INT" default={15} /><ToggleGroup bind={iterations} /></Blok>', ()),
    Check("a local passed to its port's kind", '<Blok kind="agent" context="panel"><local name="iterations" kind="INT" default={15} /><Button onClick={self.move_to(position: iterations, speed: iterations)} /></Blok>', (), dependencies=(_TYPED_SELF,)),
    Check('a local passed to a port of another kind', '<Blok kind="agent" context="panel"><local name="name" kind="STRING" default={""} /><Button onClick={self.move_to(position: name)} /></Blok>', ("local.arg_kind",), dependencies=(_TYPED_SELF,)),
    Check('a structure local of another identifier', '<Blok kind="agent" context="panel"><local name="picked" kind="STRUCTURE" identifier="@mikro/table" /><Button onClick={self.move_to(image: picked)} /></Blok>', ("local.arg_kind",), dependencies=(_TYPED_SELF,)),
    Check("a structure local of the port's identifier", '<Blok kind="agent" context="panel"><local name="picked" kind="STRUCTURE" identifier="@mikro/image" /><Button onClick={self.move_to(image: picked)} /></Blok>', (), dependencies=(_TYPED_SELF,)),
    Check("a toast on someone else's task", '<Blok kind="agent" context="panel"><Toast task={self.tasks.move_to}><done>ok</done></Toast></Blok>', ("toast.task",), dependencies=(_SELF,)),
    Check('persist is an ordinary prop', '<Blok kind="agent" context="scene"><Scene persist /></Blok>', ()),
    Check('persist where a component offers no such prop', '<Blok kind="agent" context="panel"><Card id="c" persist /></Blok>', ("catalog.props",)),
    Check('a raw palette colour', '<Blok kind="agent" context="panel"><Badge className="bg-green-500 text-white hover:bg-red-600/50" /></Blok>', ('palette.raw_colour', 'palette.raw_colour', 'palette.raw_colour')),
    Check('an arbitrary colour', '<Blok kind="agent" context="panel"><Badge className={["bg-[#fff]", task.done && "text-emerald-700"]} /></Blok>', ('palette.raw_colour', 'palette.raw_colour', 'roots.not_in_kind')),
    Check('roles are fine', '<Blok kind="agent" context="panel"><Badge className="bg-success-soft text-success data-[busy]:opacity-60 bg-brand/20" /></Blok>', ()),
    Check('a logo paints with pens', '<Blok kind="agent" context="logo"><Logo><Path d="M0 0" fill="#fbbf24" stroke="brand" /></Logo></Blok>', ("palette.logo_paint",)),
    Check("a space is an action's", '<Blok kind="agent" context="scene"><Space room="room:lab" /></Blok>', ("catalog.kind",)),
    Check('a custom blok has no run to place', '<Blok kind="custom" context="scene"><Space room="room:lab"><Placed dep="robot" /></Space></Blok>', ('catalog.kind', 'catalog.kind'), dependencies=(_ROBOT,)),
    Check('the language alone knows no kind of a component', '<Blok kind="agent" context="scene"><Space room="room:lab" /></Blok>', (), catalog=False),
    Check('an assign blok', '<Blok kind="assign" context="panel"><Card><Text>{args.slides}</Text><args /><Button onClick={assign.start()}>Start</Button></Card></Blok>', (), dependencies=(_ROBOT,), args=('slides',)),
    Check("an assign blok places the run's dependencies", '<Blok kind="assign" context="panel"><Space room="room:lab"><Placed dep="robot" /></Space></Blok>', (), dependencies=(_ROBOT,)),
    Check('an assign blok has no task', '<Blok kind="assign" context="panel"><Text>{task.status}</Text></Blok>', ("roots.not_in_kind",)),
    Check("starting a run is an assign blok's", '<Blok kind="action" context="panel"><args /><Button onClick={assign.start()}>Start</Button></Blok>', ('catalog.kind', 'assign.kind')),
    Check('a port blok', f'{_PORT}<label /><Slider bind={{port.value}} max={{args.limit}} /><Button onClick={{port.set(args.limit)}}>Longest</Button><Button onClick={{port.reset()}}>Reset</Button><Text>{{deps.robot.arm.mode}}</Text><message /></Blok>', (), dependencies=(_ROBOT,), args=('limit', 'exposure'), kinds=('port',), port=_EXPOSURE),
    Check('a port blok keeps a draft', f'{_PORT}<local name="draft" default={{0}} /><Slider bind={{draft}} /><Button onClick={{port.set(draft)}}>Take</Button></Blok>', (), dependencies=(), kinds=('port',), port=_EXPOSURE),
    Check('a port blok calls no agent', f'{_PORT}<Button onClick={{deps.robot.move_to(position: 1)}}>Go</Button></Blok>', ("calls.not_in_kind",), dependencies=(_ROBOT,), kinds=('port',), port=_EXPOSURE),
    Check('a port blok calls not itself either', f'{_PORT}<Button onClick={{self.focus() into run}}>Focus</Button></Blok>', ("calls.not_in_kind",), dependencies=(), kinds=('port',), port=_EXPOSURE),
    Check('a port blok writes the value of its port', f'{_PORT}<Slider bind={{port.touched}} /></Blok>', ("port.bind",), dependencies=(), kinds=('port',), port=_EXPOSURE),
    Check('a port blok reads the other arguments, and does not write them', f'{_PORT}<Text>{{args.gain}}</Text><Slider bind={{args.limit}} /></Blok>', ("args.unknown", "local.unknown"), dependencies=(), args=('limit', 'exposure'), kinds=('port',), port=_EXPOSURE),
    Check('the value of a port is typed as the port', f'{_PORT}<Text>{{port.value.x}}</Text><Text>{{port.value.z}}</Text><Text>{{port.frob}}</Text></Blok>', ("path.unknown", "path.unknown"), dependencies=(), kinds=('port',), port=_REGION),
    Check('a port blok is a panel with no task', '<Blok kind="port" context="panel"><Text>{task.status}</Text></Blok>', ("roots.not_in_kind",), dependencies=(), kinds=('port',)),
    Check("setting a port is a port blok's", '<Blok kind="agent" context="panel"><Button onClick={port.set(1)}>Set</Button><label /></Blok>', ("roots.not_in_kind", "catalog.kind")),
    Check('a port blok is mounted on a port', f'{_PORT}<control /></Blok>', ("kind.placement",), kinds=('agent',)),
    Check('an unknown component', '<Blok kind="agent" context="panel"><Frobnicator /></Blok>', ("catalog.unknown_component",)),
    Check('an unknown prop', '<Blok kind="agent" context="panel"><Button frob={1} /></Blok>', ("catalog.props",)),
    Check('a component outside its context', '<Blok kind="agent" context="panel"><Model /></Blok>', ('catalog.props', 'catalog.placement', 'catalog.placement')),
    Check('text is a child of whatever takes children', '<Blok kind="agent" context="panel"><Card>hi <Badge>new</Badge> there</Card><div>plain</div></Blok>', ()),
    Check('text where neither text nor children go', '<Blok kind="agent" context="panel"><Separator>x</Separator></Blok>', ("catalog.text",)),
    Check('text and its prop', '<Blok kind="agent" context="panel"><Button label="a">b</Button></Blok>', ("catalog.text",)),
    Check('text in a scene', '<Blok kind="agent" context="scene"><Scene><Model src={self.assets.scope}>x</Model></Scene></Blok>', ("catalog.placement",)),
    Check('text in a logo', '<Blok kind="agent" context="logo"><Logo><G>x</G></Logo></Blok>', ("catalog.placement",)),
    Check('the language alone takes text anywhere', '<Blok kind="agent" context="scene"><Scene><Model>x</Model><Foo>y</Foo></Scene></Blok>', (), catalog=False),
    Check('a component of a catalog the blok is not written for', '<Blok kind="agent" context="panel"><MikroSceneProbe /></Blok>', ("catalog.target",)),
    Check("a blok written for the Orkestrator's catalog", '<Blok kind="agent" context="panel" catalog="https://arkitekt.live/catalogs/orkestrator/v1"><MikroScene value={args.scene}><MikroSceneViewport /></MikroScene></Blok>', ()),
    Check('the base catalog is under every other', '<Blok kind="agent" context="panel" catalog="https://arkitekt.live/catalogs/orkestrator/v1"><Card><MikroSceneProbe /></Card></Blok>', ("catalog.placement",)),
    Check('an assign blok reads the run it prepares', '<Blok kind="assign" context="panel"><arg key="slides" /><args omit={["slides"]} given="show" /><deps /><Button disabled={!assign.ready} onClick={assign.start()}>{assign.blocked ?? "Start"}</Button></Blok>', (), args=('slides',)),
    Check('what the caller gave is told per argument', '<Blok kind="assign" context="panel"><if when={assign.given.slides}><Text>{args.slides}</Text></if><Text>{assign.given.nope}</Text><Text>{assign.nope}</Text></Blok>', ('args.unknown', 'path.unknown'), args=('slides',)),
    Check("assign is an assign blok's", '<Blok kind="action" context="panel"><Text>{assign.ready}</Text></Blok>', ("roots.not_in_kind",)),
    Check('assign is reserved', '<Blok kind="agent" context="panel"><local name="assign" kind="STRING" /><Text /></Blok>', ("roots.reserved",)),
    Check("a run's blok places the host's parts", '<Blok kind="action" context="panel" chrome="none"><status /><ask /><returns only={["image"]} /><timeline /><Button disabled={!task.can.cancel} onClick={task.cancel()}>Stop</Button><Text>{task.paused ? "Paused" : fmt.duration(task.duration)}</Text></Blok>', ()),
    Check('a task operation needs a task', '<Blok kind="agent" context="panel"><Button onClick={task.pause()} /></Blok>', ("roots.not_in_kind",)),
    Check('no chrome and no ask', '<Blok kind="action" context="panel" chrome="none"><status /></Blok>', ("chrome.no_ask",)),
    Check('a host part claims its slot even where it does not show', '<Blok kind="action" context="panel"><if when={task.running}><timeline /></if></Blok>', ("part.conditional",)),
    Check('a host part outside its kind', '<Blok kind="agent" context="panel"><returns /></Blok>', ("catalog.kind",)),
    Check('an agent says whether it is connected', '<Blok kind="custom" context="panel"><Text>{agents.robot.connected ? "Online" : "Offline"}</Text></Blok>', (), dependencies=(_ROBOT,)),
    Check('classes outside the portable subset', '<Blok kind="agent" context="panel"><Card className="before:block float-left [&>*]:p-2 grid-cols-3 translate-x-[3px] w-[12rem] -mt-[4px] space-y-2 hover:bg-muted md:flex-row dark:bg-card cursor-pointer w-48 gap-2" /></Blok>', ('class.not_portable', 'class.not_portable', 'class.not_portable', 'class.not_portable', 'class.not_portable')),
    Check('an unknown function in a namespace', '<Blok kind="agent" context="panel"><Text text={str.frobnicate(a)} /></Blok>', ('expr.unknown_function', 'roots.unknown')),
    Check('an action blok', '<Blok kind="action" context="scene"><Space room="room:lab"><Placed dep="robot"><Glow on={task.running} /></Placed></Space></Blok>', (), dependencies=(_ROBOT,)),
    Check('a placed key that is no dependency', '<Blok kind="action" context="scene"><Space room="room:lab"><Placed dep="arm" /><Route through={["robot", "arm"]} /></Space></Blok>', ('deps.unknown', 'deps.unknown'), dependencies=(_ROBOT,)),
    Check('task.answer in an agent blok', '<Blok kind="agent" context="panel"><Button onClick={task.answer(values: {})} /></Blok>', ("roots.not_in_kind",)),
    Check('a task blok answers', '<Blok kind="task" context="panel"><Button onClick={task.answer(values: {image: args.image})} /></Blok>', ()),
    Check('a "@..." string is a literal', '<Blok kind="agent" context="panel"><Text text="@self.arm.mode" /></Blok>', ()),
    Check('a string on a bool prop', '<Blok kind="agent" context="panel"><Button fullWidth="true" /></Blok>', ("props.literal_type",)),
    Check('a string on a number prop', '<Blok kind="agent" context="panel"><Progress value="50" max={100} /></Blok>', ("props.literal_type",)),
    Check('typed literals', '<Blok kind="agent" context="panel"><Card><Button fullWidth disabled={false} /><Progress value={50} max={100.5} /></Card></Blok>', ()),
    Check('a scene colour that is no role', '<Blok kind="action" context="scene"><Space room="room:lab"><Placed dep="robot"><Glow on={task.running} color="#ff3b30" /></Placed><Route through={["robot"]} color="hotpink" /></Space></Blok>', ('palette.scene_colour', 'palette.scene_colour'), dependencies=(_ROBOT,)),
    Check('an uninferrable local is ANY', '<Blok kind="agent" context="panel"><local name="x" /><Text text={x} /></Blok>', ()),
    Check('a local takes its kind from its default', '<Blok kind="agent" context="panel"><local name="n" default={3} /><Slider bind={n} /></Blok>', ()),
    Check("a local takes its task argument's port", '<Blok kind="agent" context="panel"><local name="p" /><Button onClick={self.move_to(position: p)} /></Blok>', (), dependencies=(_TYPED_SELF,)),
    Check("a structure local takes its argument's identifier", '<Blok kind="agent" context="panel"><local name="picked" /><Button onClick={self.move_to(image: picked)} /></Blok>', (), dependencies=(_TYPED_SELF,)),
    Check("a local takes its control's bind kind", '<Blok kind="agent" context="panel"><local name="on" /><Switch bind={on} /></Blok>', ()),
    Check('what a local says wins over what it infers', '<Blok kind="agent" context="panel"><local name="on" nullable description="Lights" /><Switch bind={on} /></Blok>', ()),
    Check("an inferred port keeps the local's choices", '<Blok kind="agent" context="panel"><local name="p" choices={[{"value": 1, "label": "one"}, {"value": 2, "label": "two"}]} /><Button onClick={self.move_to(position: p)} /></Blok>', (), dependencies=(_TYPED_SELF,)),
    Check('a task argument beats a bind', '<Blok kind="agent" context="panel"><local name="p" /><Card><Input bind={p} /><Button onClick={self.move_to(position: p)} /></Card></Blok>', ("local.bind_kind",), dependencies=(_TYPED_SELF,)),
    Check('the first task argument wins', '<Blok kind="agent" context="panel"><local name="p" /><Card><Button onClick={self.move_to(speed: p)} /><Button onClick={self.move_to(position: p)} /></Card></Blok>', ("local.arg_kind",), dependencies=(_TYPED_SELF,)),
    Check('a default beats a use', '<Blok kind="agent" context="panel"><local name="name" default={"a"} /><Slider bind={name} /></Blok>', ("local.bind_kind",)),
    Check('a list control tells too little', '<Blok kind="agent" context="panel"><local name="x" /><ToggleGroup bind={x} /></Blok>', ()),
    Check('an untyped argument tells nothing', '<Blok kind="agent" context="panel"><local name="p" /><Button onClick={self.move_to(position: p)} /></Blok>', (), dependencies=(_SELF,)),
    Check('a given ANY is kept', '<Blok kind="agent" context="panel"><local name="x" kind="ANY" /><Slider bind={x} /></Blok>', ()),
    Check('an ANY local skips the argument check', '<Blok kind="agent" context="panel"><local name="x" kind="ANY" /><Button onClick={self.move_to(image: x)} /></Blok>', (), dependencies=(_TYPED_SELF,)),
    Check('a path below an ANY local is taken as written', '<Blok kind="agent" context="panel"><local name="x" kind="ANY" /><Text text={x.a.b} /></Blok>', ()),
    Check('a foreach of objects without key', '<Blok kind="custom" context="panel"><foreach items={agents.shelf.inventory.slots} let="s"><Text>{s.id}</Text></foreach></Blok>', ("foreach.position_key",), dependencies=(_SHELF,)),
    Check('a keyed foreach of objects', '<Blok kind="custom" context="panel"><foreach items={agents.shelf.inventory.slots} let="s" key={s.id}><Text>{s.id}</Text></foreach></Blok>', (), dependencies=(_SHELF,)),
    Check('a foreach of scalars without key', '<Blok kind="custom" context="panel"><foreach items={agents.shelf.inventory.names} let="n"><Text>{n}</Text></foreach></Blok>', (), dependencies=(_SHELF,)),
    Check('a foreach of literal objects without key', '<Blok kind="agent" context="panel"><foreach items={[{a: 1}, {a: 2}]} let="x"><Text /></foreach></Blok>', ("foreach.position_key",)),
    Check('a foreach of unknown items without key', '<Blok kind="agent" context="panel"><foreach items={args.xs} let="x"><Text /></foreach></Blok>', ()),
    Check('an unnamed return of a record', '<Blok kind="agent" context="panel"><Card><Button onClick={self.measure() into run} /><Text text={run.returns.return0} /></Card></Blok>', ("returns.unnamed",), dependencies=(_UNNAMED,)),
    Check('an unnamed return of a latest task', '<Blok kind="agent" context="panel"><Text text={self.tasks.measure.returns.return0} /></Blok>', ("returns.unnamed",), dependencies=(_UNNAMED,)),
    Check('an unnamed return of the task', '<Blok kind="task" context="panel"><Text text={task.returns.return1} /></Blok>', ("returns.unnamed",)),
    Check('a named return', '<Blok kind="custom" context="panel"><Text text={agents.robot.tasks.move_to.returns.arrived} /></Blok>', (), dependencies=(_ROBOT,)),
    Check('coalesce reads its operands', '<Blok kind="agent" context="panel"><Text text={nothing ?? args.x} /></Blok>', ("roots.unknown",)),
    Check('spectral roles are scene colours', '<Blok kind="action" context="scene"><Space room="room:lab"><Placed dep="robot"><Glow on={task.running} color="spectrum-green" /></Placed><Route through={["robot"]} color="spectrum-violet-soft" /></Space></Blok>', (), dependencies=(_ROBOT,)),
    Check('spectral roles are classes', '<Blok kind="agent" context="panel"><Badge className="bg-spectrum-red-soft text-spectrum-red border-spectrum-cyan/40" /></Blok>', ()),
    Check('spectral pens paint a logo', '<Blok kind="agent" context="logo"><Logo><Path d="M0 0" fill="spectrum-violet" stroke="spectrum-orange" /></Logo></Blok>', ()),
    Check('scene colours are roles', '<Blok kind="action" context="scene"><Space room="room:lab"><Placed dep="robot"><Glow on={task.running} color="info" /></Placed><Route through={["robot"]} color="chart-2" /></Space></Blok>', (), dependencies=(_ROBOT,)),
    # The <Blok> itself.
    Check("a blok is a <Blok>", "<Card />", ("blok.root",)),
    Check("a blok says its kind", '<Blok context="panel"><Card /></Blok>', ("blok.attribute",)),
    Check("a kind that does not exist", '<Blok kind="widget" context="panel"><Card /></Blok>', ("blok.attribute",)),
    Check("a context that does not exist", '<Blok kind="agent" context="html"><Card /></Blok>', ("blok.attribute",)),
    Check("a kind drawn where it has no blok", '<Blok kind="task" context="scene"><Scene /></Blok>', ("context.kind",)),
    Check("a scene holds one Scene or Space", '<Blok kind="agent" context="scene"><Card /></Blok>', ("blok.content",)),
    Check("a scene holds one element", '<Blok kind="agent" context="scene"><Scene /><Scene /></Blok>', ("blok.content",)),
    Check("a logo holds one Logo", '<Blok kind="agent" context="logo"><Path d="M0 0" fill="ink" /></Blok>', ("blok.content", "catalog.placement", "catalog.placement")),
    Check("chrome is a panel's or a scene's", '<Blok kind="agent" context="logo" chrome="none"><Logo /></Blok>', ("chrome.context",)),
    Check("an action logo has no dependencies", '<Blok kind="action" context="logo"><Logo><Path d="M0 0" fill={task.running ? "brand" : "muted"} /></Logo></Blok>', ("manifest.action_logo",), dependencies=(_ROBOT,)),
    Check("mounted where its kind does not go", _PANEL + "<Card /></Blok>", ("kind.placement",), kinds=("action", "assign")),
    Check("mounted where its kind goes", '<Blok kind="assign" context="panel"><args /></Blok>', (), kinds=("action", "assign")),
    # Mounted nowhere: what a mount provides is taken as written.
    Check("unmounted, dependencies are open", '<Blok kind="action" context="panel"><Text>{deps.robot.arm.mode}</Text><Button onClick={deps.stage.move(x: 1)} /></Blok>', (), dependencies=None),
    Check("unmounted, a placed key is open", '<Blok kind="action" context="scene"><Space room="room:lab"><Placed dep="arm" /></Space></Blok>', (), dependencies=None),
    Check("unmounted, an agent blok still reads only the dependencies it declares", '<Blok kind="agent" context="panel"><Text>{deps.robot.arm.mode}</Text></Blok>', ("deps.unknown",), dependencies=None),
    Check("unmounted, a local is still checked", '<Blok kind="action" context="panel"><local name="show" default={true} persist /><Switch bind={shw} /></Blok>', ("local.unknown", "roots.unknown", "local.unused_persist"), dependencies=None),
    Check("unmounted, like borrows nothing", '<Blok kind="action" context="panel"><local name="p" like="deps.robot.move_to.position" /><Text>{p}</Text></Blok>', (), dependencies=None),
    # Locals in scope.
    Check("a local is in scope in its parent's subtree", _PANEL + '<Card><local name="open" default={false} /><Switch bind={open} /><Card><Text>{open}</Text></Card></Card></Blok>', ()),
    Check("a local is out of scope beside its parent", _PANEL + '<Card><local name="open" default={false} /><Switch bind={open} /></Card><Text>{open}</Text></Blok>', ("roots.unknown",)),
    Check("a bind out of scope writes no local", _PANEL + '<Card><local name="open" default={false} /></Card><Switch bind={open} /></Blok>', ("local.unknown", "roots.unknown")),
    Check("a local in a foreach is one per item", _PANEL + '<foreach items={args.xs} let="x" key={x.id}><local name="open" default={false} persist /><Switch bind={open} /><Text>{x.id}</Text></foreach></Blok>', ()),
    Check("a kept local in a keyless foreach", _PANEL + '<foreach items={args.xs} let="x"><local name="open" default={false} persist /><Switch bind={open} /></foreach></Blok>', ("local.persist_id",)),
    Check("a local comes first among its siblings", _PANEL + '<Card><Text /><local name="a" default={1} /><Slider bind={a} /></Card></Blok>', ("local.position",)),
    Check("a local in a component that takes no children", _PANEL + '<Separator><local name="a" default={1} /></Separator></Blok>', ("local.position",)),
    Check("a local shadowing an outer local", _PANEL + '<local name="a" default={1} /><Card><local name="a" default={2} /><Slider bind={a} /></Card><Slider bind={a} /></Blok>', ("roots.shadow",)),
    Check("a local shadowing a let", _PANEL + '<foreach items={args.xs} let="x" key={x}><local name="x" default={1} /></foreach></Blok>', ("roots.shadow",)),
    Check("a local shadowing a record", _PANEL + '<local name="move" default={1} /><Button onClick={self.move(to: 1) into move} /></Blok>', ("roots.shadow",), dependencies=(_TO,)),
    Check("a local's attribute is a literal", _PANEL + '<local name="a" default={args.x} /></Blok>', ("local.attribute",)),
    Check("a local has no such field", _PANEL + '<local name="a" colour="red" /></Blok>', ("local.attribute", "catalog.props")),
    Check("an action logo has no locals", '<Blok kind="action" context="logo"><local name="a" default={1} /><Logo /></Blok>', ("roots.not_in_kind",)),
    Check("a local's kind from a use in its scope only", _PANEL + '<Card><local name="p" /><Button onClick={self.move(to: p)} /></Card></Blok>', (), dependencies=(_TO,)),
    Check("like borrows an argument's port", _PANEL + '<local name="p" like="self.move.to" /><Slider bind={p} /><Button onClick={self.move(to: p)} /></Blok>', (), dependencies=(_TO,)),
    Check("like names an action the mount does not have", _PANEL + '<local name="p" like="self.fly.to" /><Text>{p}</Text></Blok>', ("local.like",), dependencies=(_TO,)),
    Check("like is no argument", _PANEL + '<local name="p" like="move.to" /><Text>{p}</Text></Blok>', ("local.like",), dependencies=(_TO,)),
    # Queries and subscriptions.
    Check("a query is a child of the blok", _PANEL + '<Card><query name="q" service="mikro" document="query { a }" /></Card></Blok>', ("query.position",)),
    Check("a query declared twice", _PANEL + '<query name="q" service="mikro" document="query { a }" /><query name="q" service="mikro" document="query { b }" /></Blok>', ("query.duplicate",)),
    Check("a query needs its document", _PANEL + '<query name="q" service="mikro" /></Blok>', ("query.declaration", "catalog.props")),
    Check("a query that writes", _PANEL + '<query name="q" service="mikro" document="mutation { a }" /></Blok>', ("query.read_only",)),
    Check("a subscription that is a query", _PANEL + '<subscription name="s" service="mikro" document="query { a }" /></Blok>', ("query.kind",)),
    Check("a subscription replays a declared query", _PANEL + '<subscription name="s" service="mikro" document="subscription { a }" history="q" /></Blok>', ("query.history",)),
    Check("a var outside a query", _PANEL + '<var key="a" value={1} /></Blok>', ("query.declaration", "catalog.placement")),
    Check("a query variable reads blok-level locals", _PANEL + '<local name="slide" default={"1"} /><query name="q" service="mikro" document="query ($s: ID) { a(s: $s) }"><var key="s" value={slide} /></query><Text>{queries.q.status}</Text></Blok>', ()),
)


# --------------------------------------------------------------------------- building


def _tree(node: ComponentNodeInput) -> JSON:
    dumped: JSON = node.model_dump(mode="json", exclude_none=True)
    return dumped


def _mount(check: Check) -> dict[str, JSON]:
    return {
        "kinds": list(check.kinds),
        "dependencies": [dict(dependency) for dependency in check.dependencies] if check.dependencies is not None else None,
        "args": list(check.args) if check.args is not None else None,
        "catalog": check.catalog,
        "port": dict(check.port) if check.port is not None else None,
        "workflows": {action: [dict(dependency) for dependency in dependencies] for action, dependencies in check.workflows.items()} if check.workflows is not None else None,
    }


def _target(tree: ComponentNodeInput) -> str | None:
    return blok_catalog(tree)


def _view(check: Check) -> BlokView | None:
    if not check.catalog:
        return None
    target = _target(_parse(check))
    return blok_view(target) if target is not None else default_view()


def _parse(check: Check) -> ComponentNodeInput:
    """The case's tree, parsed with the view of the catalog its ``<Blok>`` names."""
    return parse_bsx(check.source)


def _check_scope(check: Check) -> "BlokScope":
    from arkitekt_spec.declare.blok.validate import BlokScope

    return BlokScope(
        kinds=check.kinds,
        dependencies=tuple(AgentDependencyInput.model_validate(dict(d)) for d in check.dependencies) if check.dependencies is not None else None,
        args=check.args,
        port=PortMatchInput.model_validate(dict(check.port)) if check.port is not None else None,
        workflows={action: tuple(AgentDependencyInput.model_validate(dict(d)) for d in dependencies) for action, dependencies in check.workflows.items()}
        if check.workflows is not None
        else None,
    )


def _resolved(check: Check, view: BlokView | None) -> dict[str, JSON]:
    from arkitekt_spec.declare.blok.infer import resolve_locals
    from arkitekt_spec.declare.blok.validate import bound_scope

    scope = bound_scope(_parse(check), _check_scope(check))
    locals_ = resolve_locals(_parse(check), scope.dependencies, view)
    return {"locals": {node: each.local.model_dump(mode="json", exclude_none=True) for node, each in locals_.items()}}


def resolved(check: Check) -> dict[str, JSON]:
    """Each ``<local>``'s port with its kind resolved, by node id
    (:func:`~arkitekt_spec.declare.blok.infer.resolve_locals`)."""
    return _resolved(check, _view(check))


def language_resolved(check: Check) -> dict[str, JSON]:
    """The same with no catalog at all: what a server resolves a blok's locals to. A local only
    a control's ``bind`` would type stays ``ANY``."""
    return _resolved(check, None)


def language_codes(check: Check) -> list[str]:
    """The codes the language alone finds for ``check``: no catalog is read, as a server checks it."""
    from arkitekt_spec.declare.blok.validate import check_blok_tree

    return [finding.code for finding in check_blok_tree(_parse(check), _check_scope(check), None)]


def check_codes(check: Check) -> list[str]:
    """The codes the validator finds for ``check``."""
    from arkitekt_spec.declare.blok.validate import check_blok_tree

    return [finding.code for finding in check_blok_tree(_parse(check), _check_scope(check), _view(check))]


def build_v2() -> dict[str, JSON]:
    """The v2 corpus: parsed, refused and checked cases.

    Raises:
        ValueError: If a case does not do what it is listed as doing (the spec disagrees with
            its own corpus).
    """
    vocabulary = load_vocabulary()
    cases: list[JSON] = []
    for name, source in VALID:
        tree = parse_bsx(source)
        printed = to_bsx(tree)
        if parse_bsx(printed) != tree:
            raise ValueError(f"{name}: does not print back into its tree")
        cases.append({"name": name, "source": source, "tree": _tree(tree), "printed": printed})
    for name, source, code in REFUSED:
        try:
            parse_bsx(source)
        except BlokError as error:
            if error.code != code:
                raise ValueError(f"{name}: refused with {error.code}, listed as {code}") from error
            cases.append({"name": name, "source": source, "error": {"code": error.code, "line": error.line, "column": error.column}})
        else:
            raise ValueError(f"{name}: parsed, listed as refused with {code}")
    for check in CHECKS:
        codes = check_codes(check)
        if tuple(codes) != check.codes:
            raise ValueError(f"{check.name}: finds {codes}, listed as {list(check.codes)}")
        cases.append({
            "name": check.name,
            "source": check.source,
            "tree": _tree(_parse(check)),
            "mount": _mount(check),
            "resolved": resolved(check),
            "codes": list[JSON](codes),
            # What a server makes of it: the language's rules, and no catalog.
            "language": {"resolved": language_resolved(check), "codes": list[JSON](language_codes(check))},
        })
    return {
        "vocab": vocabulary.vocab,
        "minor": vocabulary.minor,
        "catalogs": {"base": vocabulary.base_catalog, "bundled": dict(vocabulary.bundled_catalogs)},
        "cases": cases,
    }


def write(directory: Path) -> Path:
    """Write ``bsx_v2.json`` into ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    v2 = directory / "bsx_v2.json"
    v2.write_text(json.dumps(build_v2(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return v2


if __name__ == "__main__":  # pragma: no cover
    print(write(Path(sys.argv[1] if len(sys.argv) > 1 else ".")))
