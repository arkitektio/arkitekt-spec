"""The parity corpus of the blok language: what the spec makes of v2 sources.

Three parsers implement v2 -- this package, takt (Rust) and orkestrator (TypeScript). The
corpus is how they agree: every case is a source with the tree the spec parses it into, or the
code it is refused with, or (for checks) the codes the validator finds. Parity compares trees
and codes, never message text.

::

    python -m arkitekt_spec.blok.corpus OUT_DIR    # writes bsx_v2.json

The file is self-describing: it carries the vocab version and the catalogs the spec checked
with. Parsing needs no catalog: text is stored as nodes. A checked
case's scope has ``catalog``: whether it is checked against a catalog or held to the language
alone, and ``target``: the id of the catalog it is written for, or null for the base. A checked case's
``resolved`` is its scope after inference (``locals`` each with its kind): what a checker checks
against. ``language`` is the same with no catalog read at all (``resolved`` and ``codes``):
what a server, which holds a blok to the language alone, finds and stores. Dependencies are never inferred.
"""

import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from arkitekt_spec.actions import (
    AgentDependencyInput,
    BlokLocalInput,
    BlokQueryInput,
    BlokSubscriptionInput,
    ComponentNodeInput,
)
from arkitekt_spec.blok.bsx import parse_bsx
from arkitekt_spec.blok.catalogs import ORKESTRATOR_CATALOG_ID, BlokView, blok_view, default_view
from arkitekt_spec.blok.unparse import to_bsx
from arkitekt_spec.blok.vocabulary import JSON, BlokError, load_vocabulary

if TYPE_CHECKING:
    from arkitekt_spec.declare.blok.validate import BlokScope

# --------------------------------------------------------------------------- v2: parsed


VALID: Sequence[tuple[str, str]] = (
    ("an element", "<Card />"),
    ("an explicit id", '<Card id="ot2-monitor"><Button /></Card>'),
    ("ids by name and occurrence", "<Card>\n  <Button />\n  <Text />\n  <Button />\n</Card>"),
    ("a literal is a literal", '<Card className="p-4"><Text text="@fff" /></Card>'),
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
    ("a space", '<Space room="room:lab"><Placed dep="robot"><Glow on={task.running} /></Placed><Route through={["robot", "ot2"]} /></Space>'),
    ("a logo", '<AgentLogo viewBox="0 0 64 64"><G fill="ink"><Rect x="0" y="0" width="64" height="64" /></G></AgentLogo>'),
    ("comments", "<Card><!-- a comment --><Button />{/* another */}</Card>"),
    ("a multi-line attribute", '<Card>\n  <Button\n    label="multi\n line"\n  />\n</Card>'),
    ("a trailing comma", "<Text text={str.concat([a, b,])} />"),
    ("a null argument", "<Button onClick={self.go(to: null)} />"),
    ("a foreach without key", '<foreach items={self.names} let="n"><Text>{n}</Text></foreach>'),
    ("coalesce", "<Text text={a ?? b} />"),
    ("a coalesce chain is one call", "<Text text={a ?? b ?? c} />"),
    ("parentheses keep a coalesce group", "<Text text={(a ?? b) ?? c} />"),
    ("coalesce binds looser than or", "<Text text={a || b ?? c} />"),
    ("coalesce binds tighter than a ternary", '<Badge text={a ?? b ? "x" : "y"} />'),
    ("coalesce inside and", "<Badge when={(a ?? b) && c} />"),
    ("logic.coalesce written as the call", "<Text text={logic.coalesce(a, b)} />"),
    ("spectral pens", '<AgentLogo><Path d="M0 0" fill="spectrum-red" stroke="spectrum-blue" /></AgentLogo>'),
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
    ("a sequence", "<Button onClick={self.a(); self.b()} />", "callback.sequence"),
    ("an assignment", '<Button onClick={camera = "iso"} />', "callback.assignment"),
    ("await", "<Button onClick={await self.a()} />", "callback.await"),
    ("a positional task argument", "<Button onClick={self.go(1)} />", "callback.positional"),
    ("a task call inside an expression", "<Text text={str.upper(self.go(x: 1))} />", "callback.nested"),
    ("an effect inside an expression", "<Text text={str.upper(ui.copy(x))} />", "callback.nested"),
    ("into a path", "<Button onClick={task.status into run} />", "callback.into"),
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
    """A source checked against a scope: the codes the validator finds, in order."""

    name: str
    source: str
    codes: tuple[str, ...]
    kind: str = "agent"
    context: str = "HTML"
    dependencies: tuple[Mapping[str, JSON], ...] = ()
    locals: tuple[Mapping[str, JSON], ...] = ()
    queries: tuple[Mapping[str, JSON], ...] = ()
    subscriptions: tuple[Mapping[str, JSON], ...] = ()
    args: tuple[str, ...] | None = None
    catalog: bool = True
    """Whether it is checked against a catalog at all, or held to the language alone."""
    target: str | None = None
    """The id of the catalog it is written for; None: the base catalog."""


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
_UNNAMED: Mapping[str, JSON] = {
    "key": "self",
    "action_dependencies": [{"key": "measure", "demand": {"return_matches": [{"key": "return0", "kind": "FLOAT"}]}}],
}
_FORM: Mapping[str, JSON] = {"key": "form", "kind": "DICT", "children": [{"key": "name", "kind": "STRING"}, {"key": "count", "kind": "INT"}]}

CHECKS: Sequence[Check] = (
    Check("a clean agent blok", '<Html><Card><Text>{self.arm.mode}</Text><Button onClick={self.move_to(position: 1) into move}>Go</Button><Progress value={move.progress} /></Card></Html>', (), dependencies=(_SELF,)),
    Check("deps in an agent blok", "<Html><Text text={deps.robot.arm.mode} /></Html>", ("roots.not_in_kind",), dependencies=(_ROBOT,)),
    Check("self in a custom blok", "<Html><Text text={self.arm.mode} /></Html>", ("roots.not_in_kind",), kind="custom", dependencies=(_ROBOT,)),
    Check("task in an agent blok", "<Html><Text text={task.status} /></Html>", ("roots.not_in_kind",)),
    Check("an action logo reads only task", '<ActionLogo><Path d="M0 0" fill={task.running ? "brand" : "muted"} stroke={viewer.user.id} /></ActionLogo>', ("roots.not_in_kind",), kind="action_logo", context="ACTION_LOGO"),
    Check("an unknown root", "<Html><Text text={nothing.here} /></Html>", ("roots.unknown",)),
    Check("a function namespace is no value", "<Html><Text text={math.pi} /></Html>", ("roots.unknown",)),
    Check("event outside a callback", "<Html><Text text={event.value} /></Html>", ("roots.event_outside_callback",)),
    Check("error outside its slot", "<Html><Text text={error.message} /></Html>", ("roots.error_outside_boundary",)),
    Check("a reserved local", "<Html><Text /></Html>", ("roots.reserved",), locals=({"key": "task", "kind": "STRING"},)),
    Check("then is reserved", "<Html><Text /></Html>", ("roots.reserved",), locals=({"key": "then", "kind": "STRING"},)),
    Check("query is reserved", "<Html><Text /></Html>", ("roots.reserved",), locals=({"key": "query", "kind": "STRING"},)),
    Check("a let shadowing a local", '<Html><foreach items={args.xs} let="x" key={x}><Text /></foreach></Html>', ("roots.shadow",), locals=({"key": "x", "kind": "STRING"},)),
    Check("one record, two calls", "<Html><Card><Button onClick={self.move_to(position: 1) into move} /><Button onClick={self.move_to(position: 2) into move} /></Card></Html>", ("into.twice",), dependencies=(_SELF,)),
    Check("an unknown dependency", "<Html><Text text={deps.nope.x} /></Html>", ("deps.unknown",), kind="custom", dependencies=(_ROBOT,)),
    Check("a custom blok reading an undeclared dependency", "<Html><Card><Text text={deps.robot.arm.mode} /><Button onClick={deps.stage.move(x: 1)} /></Card></Html>", ("deps.unknown", "deps.unknown"), kind="custom"),
    Check("an undeclared dependency beside a declared one", "<Html><Card><Text text={deps.robot.arm.mode} /><Text text={deps.camera.frame} /></Card></Html>", ("deps.unknown",), kind="custom", dependencies=(_ROBOT,)),
    Check("a query variable reading an undeclared dependency", "<Html><Text text={queries.positions.status} /></Html>", ("deps.unknown",), kind="custom", queries=({**_POSITIONS, "variables": [{"key": "agent", "value_path": "deps.stage.id"}]},)),
    Check("an unknown state", "<Html><Text text={deps.robot.legs} /></Html>", ("deps.unknown_state",), kind="custom", dependencies=(_ROBOT,)),
    Check("an unknown action", "<Html><Button onClick={deps.robot.fly()} /></Html>", ("deps.unknown_action",), kind="custom", dependencies=(_ROBOT,)),
    Check("an unknown field", "<Html><Text text={deps.robot.arm.speed} /></Html>", ("path.unknown",), kind="custom", dependencies=(_ROBOT,)),
    Check("a task record field", "<Html><Text text={deps.robot.tasks.move_to.returns.arrived} /></Html>", (), kind="custom", dependencies=(_ROBOT,)),
    Check("an unknown record field", "<Html><Card><Button onClick={self.move_to(position: 1) into move} /><Text text={move.result} /></Card></Html>", ("path.unknown",), dependencies=(_SELF,)),
    Check("an undeclared arg", "<Html><Text text={args.image} /></Html>", ("args.unknown",), args=("dataset",)),
    Check("an args read whole", "<Html><Text text={args} /></Html>", ("roots.whole",)),
    Check("an undeclared query", "<Html><Text text={queries.cells.value} /></Html>", ("query.unknown",)),
    Check("a declared query", "<Html><Card><Text text={queries.positions.status} /><Button onClick={queries.positions.refetch()} /></Card></Html>", (), queries=(_POSITIONS,)),
    Check("refetch of an undeclared query", "<Html><Button onClick={queries.cells.refetch()} /></Html>", ("query.unknown",)),
    Check("fetch_more of a paged query", "<Html><Button onClick={queries.positions.fetch_more(after: queries.positions.value.cursor)} /></Html>", (), queries=(_PAGED,)),
    Check("fetch_more of a query without more", "<Html><Button onClick={queries.positions.fetch_more()} /></Html>", ("query.fetch_more",), queries=(_POSITIONS,)),
    Check("fetch_more of an undeclared query", "<Html><Button onClick={queries.cells.fetch_more()} /></Html>", ("query.unknown",)),
    Check("a query effect named by a path", "<Html><Button onClick={query.refetch(args.which)} /></Html>", ("query.effect_target",)),
    Check("fetching_more is a field of the record", "<Html><Spinner when={queries.positions.fetching_more} /></Html>", (), queries=(_PAGED,)),
    Check("then is the viewed moment", "<Html><Text text={then} /></Html>", ()),
    Check("then has no fields", "<Html><Text text={then.year} /></Html>", ("path.unknown",)),
    Check("then in an action logo", '<ActionLogo><Path d="M0 0" fill={then ? "muted" : "brand"} /></ActionLogo>', ("roots.not_in_kind",), kind="action_logo", context="ACTION_LOGO"),
    Check("now is no root", "<Html><Text text={now} /></Html>", ("roots.unknown",)),
    Check("time.since is gone", "<Html><Text text={time.since(task.message)} /></Html>", ("expr.unknown_function", "roots.not_in_kind")),
    Check("time.diff is pure", "<Html><Text text={fmt.duration(time.diff(args.start, args.end))} /></Html>", ()),
    Check("a time component", "<Html><Time value={args.started} relative /></Html>", ()),
    Check("a callback holding a value", '<Html><Button onClick={"x"} /></Html>', ("callback.unbound",)),
    Check("a task call in a value prop", "<Html><Text text={self.move_to(position: 1)} /></Html>", ("callback.not_callback",), dependencies=(_SELF,)),
    Check("an unknown event field", "<Html><Slider onChange={self.move_to(position: event.nope)} /></Html>", ("callback.event_field",), dependencies=(_SELF,)),
    Check("bind to no local", "<Html><Slider bind={deps.robot.arm} /></Html>", ("local.unknown",), kind="custom", dependencies=(_ROBOT,)),
    Check("a bind that is a literal", '<Html><Slider bind="iterations" /></Html>', ("local.bind",), locals=(_ITERATIONS,)),
    Check("a default that does not fit its kind", "<Html><Slider bind={iterations} /></Html>", ("local.default",), locals=({"key": "iterations", "kind": "INT", "default": "15"},)),
    Check("a persisted local nothing binds", "<Html><Text /></Html>", ("local.unused_persist",), locals=({**_ITERATIONS, "persist": True},)),
    Check("a slider binds an int local", "<Html><Slider bind={iterations} /></Html>", (), locals=(_ITERATIONS,)),
    Check("an input binds a string local", "<Html><Input bind={name} /></Html>", (), locals=(_NAME,)),
    Check("a switch bound to an int local", "<Html><Switch bind={iterations} /></Html>", ("local.bind_kind",), locals=(_ITERATIONS,)),
    Check("an input bound to an int local", "<Html><Input bind={iterations} /></Html>", ("local.bind_kind",), locals=(_ITERATIONS,)),
    Check("a slider bound to a string local", "<Html><Slider bind={name} /></Html>", ("local.bind_kind",), locals=(_NAME,)),
    Check("a bind checks the field it writes", "<Html><Card><Input bind={form.name} /><Switch bind={form.count} /></Card></Html>", ("local.bind_kind",), locals=(_FORM,)),
    Check("an ANY control binds any local", "<Html><ToggleGroup bind={iterations} /></Html>", (), locals=(_ITERATIONS,)),
    Check("a local passed to its port's kind", "<Html><Button onClick={self.move_to(position: iterations, speed: iterations)} /></Html>", (), locals=(_ITERATIONS,), dependencies=(_TYPED_SELF,)),
    Check("a local passed to a port of another kind", "<Html><Button onClick={self.move_to(position: name)} /></Html>", ("local.arg_kind",), locals=(_NAME,), dependencies=(_TYPED_SELF,)),
    Check("a structure local of another identifier", "<Html><Button onClick={self.move_to(image: picked)} /></Html>", ("local.arg_kind",), locals=({"key": "picked", "kind": "STRUCTURE", "identifier": "@mikro/table"},), dependencies=(_TYPED_SELF,)),
    Check("a structure local of the port's identifier", "<Html><Button onClick={self.move_to(image: picked)} /></Html>", (), locals=({"key": "picked", "kind": "STRUCTURE", "identifier": "@mikro/image"},), dependencies=(_TYPED_SELF,)),
    Check("a toast on someone else's task", "<Html><Toast task={self.tasks.move_to}><done>ok</done></Toast></Html>", ("toast.task",), dependencies=(_SELF,)),
    Check("persist is an ordinary prop", "<Scene persist />", (), context="SCENE"),
    Check("persist where a component offers no such prop", '<Html><Card id="c" persist /></Html>', ("catalog.props",)),
    Check("a raw palette colour", '<Html><Badge className="bg-green-500 text-white hover:bg-red-600/50" /></Html>', ("palette.raw_colour", "palette.raw_colour", "palette.raw_colour")),
    Check("an arbitrary colour", '<Html><Badge className={["bg-[#fff]", task.done && "text-emerald-700"]} /></Html>', ("palette.raw_colour", "palette.raw_colour", "roots.not_in_kind")),
    Check("roles are fine", '<Html><Badge className="bg-success-soft text-success data-[busy]:opacity-60 bg-brand/20" /></Html>', ()),
    Check("a logo paints with pens", '<AgentLogo><Path d="M0 0" fill="#fbbf24" stroke="brand" /></AgentLogo>', ("palette.logo_paint",), context="LOGO"),
    Check("a scene blok's root", "<Card />", ("context.root",), context="SCENE"),
    Check("an html blok's root", "<Card />", ("context.root",)),
    Check("an action logo's root", '<AgentLogo><Path d="M0 0" fill="brand" /></AgentLogo>', ("context.root",), kind="action_logo", context="ACTION_LOGO"),
    Check("a space is an action's", '<Space room="room:lab" />', ("catalog.kind",), context="SCENE"),
    Check("a custom blok has no run to place", '<Space room="room:lab"><Placed dep="robot" /></Space>', ("catalog.kind", "catalog.kind"), kind="custom", context="SCENE", dependencies=(_ROBOT,)),
    Check("the language alone knows no kind of a component", '<Space room="room:lab" />', (), context="SCENE", catalog=False),
    Check("an assign blok", '<Html><Card><Text>{args.slides}</Text><args /><Button onClick={assign.start()}>Start</Button></Card></Html>', (), kind="assign", dependencies=(_ROBOT,), args=("slides",)),
    Check("an assign blok places the run's dependencies", '<Html><Space room="room:lab"><Placed dep="robot" /></Space></Html>', (), kind="assign", dependencies=(_ROBOT,)),
    Check("an assign blok has no task", "<Html><Text>{task.status}</Text></Html>", ("roots.not_in_kind",), kind="assign"),
    Check("starting a run is an assign blok's", "<Html><args /><Button onClick={assign.start()}>Start</Button></Html>", ("catalog.kind", "assign.kind"), kind="action"),
    Check("an unknown component", "<Html><Frobnicator /></Html>", ("catalog.unknown_component",)),
    Check("an unknown prop", "<Html><Button frob={1} /></Html>", ("catalog.props",)),
    Check("a component outside its context", "<Html><Model /></Html>", ("catalog.props", "catalog.placement", "catalog.placement")),
    Check("text is a child of whatever takes children", "<Html><Card>hi <Badge>new</Badge> there</Card><div>plain</div></Html>", ()),
    Check("text where neither text nor children go", "<Html><Separator>x</Separator></Html>", ("catalog.text",)),
    Check("text and its prop", '<Html><Button label="a">b</Button></Html>', ("catalog.text",)),
    Check("text in a scene", "<Scene><Model src={self.assets.scope}>x</Model></Scene>", ("catalog.placement",), context="SCENE"),
    Check("text in a logo", "<AgentLogo><G>x</G></AgentLogo>", ("catalog.placement",), context="LOGO"),
    Check("the language alone takes text anywhere", "<Scene><Model>x</Model><Foo>y</Foo></Scene>", (), context="SCENE", catalog=False),
    Check("a component of a catalog the blok is not written for", "<Html><MikroSceneProbe /></Html>", ("catalog.target",)),
    Check("a blok written for the Orkestrator's catalog", "<Html><MikroScene value={args.scene}><MikroSceneViewport /></MikroScene></Html>", (), target=ORKESTRATOR_CATALOG_ID),
    Check("the base catalog is under every other", "<Html><Card><MikroSceneProbe /></Card></Html>", ("catalog.placement",), target=ORKESTRATOR_CATALOG_ID),
    Check("an assign blok reads the run it prepares", '<Html><arg key="slides" /><args omit={["slides"]} given="show" /><deps /><Button disabled={!assign.ready} onClick={assign.start()}>{assign.blocked ?? "Start"}</Button></Html>', (), kind="assign", args=("slides",)),
    Check("what the caller gave is told per argument", "<Html><if when={assign.given.slides}><Text>{args.slides}</Text></if><Text>{assign.given.nope}</Text><Text>{assign.nope}</Text></Html>", ("args.unknown", "path.unknown"), kind="assign", args=("slides",)),
    Check("assign is an assign blok's", "<Html><Text>{assign.ready}</Text></Html>", ("roots.not_in_kind",), kind="action"),
    Check("assign is reserved", "<Html><Text /></Html>", ("roots.reserved",), locals=({"key": "assign", "kind": "STRING"},)),
    Check("a run's blok places the host's parts", '<Html chrome="none"><status /><ask /><returns only={["image"]} /><timeline /><Button disabled={!task.can.cancel} onClick={task.cancel()}>Stop</Button><Text>{task.paused ? "Paused" : fmt.duration(task.duration)}</Text></Html>', (), kind="action"),
    Check("a task operation needs a task", "<Html><Button onClick={task.pause()} /></Html>", ("roots.not_in_kind",)),
    Check("no chrome and no ask", '<Html chrome="none"><status /></Html>', ("chrome.no_ask",), kind="action"),
    Check("a host part claims its slot even where it does not show", "<Html><if when={task.running}><timeline /></if></Html>", ("part.conditional",), kind="action"),
    Check("a host part outside its kind", "<Html><returns /></Html>", ("catalog.kind",)),
    Check("an agent says whether it is connected", '<Html><Text>{deps.robot.connected ? "Online" : "Offline"}</Text></Html>', (), kind="custom", dependencies=(_ROBOT,)),
    Check("classes outside the portable subset", '<Html><Card className="before:block float-left [&>*]:p-2 grid-cols-3 translate-x-[3px] w-[12rem] -mt-[4px] space-y-2 hover:bg-muted md:flex-row dark:bg-card cursor-pointer w-48 gap-2" /></Html>', ("class.not_portable",) * 5),
    Check("an unknown function in a namespace", "<Html><Text text={str.frobnicate(a)} /></Html>", ("expr.unknown_function", "roots.unknown")),
    Check("an action blok", '<Space room="room:lab"><Placed dep="robot"><Glow on={task.running} /></Placed></Space>', (), kind="action", context="SCENE", dependencies=(_ROBOT,)),
    Check("a placed key that is no dependency", '<Space room="room:lab"><Placed dep="arm" /><Route through={["robot", "arm"]} /></Space>', ("deps.unknown", "deps.unknown"), kind="action", context="SCENE", dependencies=(_ROBOT,)),
    Check("task.answer in an agent blok", "<Html><Button onClick={task.answer(values: {})} /></Html>", ("roots.not_in_kind",)),
    Check("a task blok answers", '<Html><Button onClick={task.answer(values: {image: args.image})} /></Html>', (), kind="task"),
    Check("a \"@...\" string is a literal", '<Html><Text text="@self.arm.mode" /></Html>', ()),
    Check("a string on a bool prop", '<Html><Button fullWidth="true" /></Html>', ("props.literal_type",)),
    Check("a string on a number prop", '<Html><Progress value="50" max={100} /></Html>', ("props.literal_type",)),
    Check("typed literals", '<Html><Card><Button fullWidth disabled={false} /><Progress value={50} max={100.5} /></Card></Html>', ()),
    Check("a scene colour that is no role", '<Space room="room:lab"><Placed dep="robot"><Glow on={task.running} color="#ff3b30" /></Placed><Route through={["robot"]} color="hotpink" /></Space>', ("palette.scene_colour", "palette.scene_colour"), kind="action", context="SCENE", dependencies=(_ROBOT,)),
    Check("an uninferrable local is ANY", "<Html><Text text={x} /></Html>", (), locals=({"key": "x"},)),
    Check("a local takes its kind from its default", "<Html><Slider bind={n} /></Html>", (), locals=({"key": "n", "default": 3},)),
    Check("a local takes its task argument's port", "<Html><Button onClick={self.move_to(position: p)} /></Html>", (), locals=({"key": "p"},), dependencies=(_TYPED_SELF,)),
    Check("a structure local takes its argument's identifier", "<Html><Button onClick={self.move_to(image: picked)} /></Html>", (), locals=({"key": "picked"},), dependencies=(_TYPED_SELF,)),
    Check("a local takes its control's bind kind", "<Html><Switch bind={on} /></Html>", (), locals=({"key": "on"},)),
    Check("what a local says wins over what it infers", "<Html><Switch bind={on} /></Html>", (), locals=({"key": "on", "nullable": True, "description": "Lights"},)),
    Check("an inferred port keeps the local's choices", "<Html><Button onClick={self.move_to(position: p)} /></Html>", (), locals=({"key": "p", "choices": [{"value": 1, "label": "one"}, {"value": 2, "label": "two"}]},), dependencies=(_TYPED_SELF,)),
    Check("a task argument beats a bind", "<Html><Card><Input bind={p} /><Button onClick={self.move_to(position: p)} /></Card></Html>", ("local.bind_kind",), locals=({"key": "p"},), dependencies=(_TYPED_SELF,)),
    Check("the first task argument wins", "<Html><Card><Button onClick={self.move_to(speed: p)} /><Button onClick={self.move_to(position: p)} /></Card></Html>", ("local.arg_kind",), locals=({"key": "p"},), dependencies=(_TYPED_SELF,)),
    Check("a default beats a use", "<Html><Slider bind={name} /></Html>", ("local.bind_kind",), locals=({"key": "name", "default": "a"},)),
    Check("a list control tells too little", "<Html><ToggleGroup bind={x} /></Html>", (), locals=({"key": "x"},)),
    Check("an untyped argument tells nothing", "<Html><Button onClick={self.move_to(position: p)} /></Html>", (), locals=({"key": "p"},), dependencies=(_SELF,)),
    Check("a given ANY is kept", "<Html><Slider bind={x} /></Html>", (), locals=({"key": "x", "kind": "ANY"},)),
    Check("an ANY local skips the argument check", "<Html><Button onClick={self.move_to(image: x)} /></Html>", (), locals=({"key": "x", "kind": "ANY"},), dependencies=(_TYPED_SELF,)),
    Check("a path below an ANY local is taken as written", "<Html><Text text={x.a.b} /></Html>", (), locals=({"key": "x", "kind": "ANY"},)),
    Check("a foreach of objects without key", '<Html><foreach items={deps.shelf.inventory.slots} let="s"><Text>{s.id}</Text></foreach></Html>', ("foreach.position_key",), kind="custom", dependencies=(_SHELF,)),
    Check("a keyed foreach of objects", '<Html><foreach items={deps.shelf.inventory.slots} let="s" key={s.id}><Text>{s.id}</Text></foreach></Html>', (), kind="custom", dependencies=(_SHELF,)),
    Check("a foreach of scalars without key", '<Html><foreach items={deps.shelf.inventory.names} let="n"><Text>{n}</Text></foreach></Html>', (), kind="custom", dependencies=(_SHELF,)),
    Check("a foreach of literal objects without key", '<Html><foreach items={[{a: 1}, {a: 2}]} let="x"><Text /></foreach></Html>', ("foreach.position_key",)),
    Check("a foreach of unknown items without key", '<Html><foreach items={args.xs} let="x"><Text /></foreach></Html>', ()),
    Check("an unnamed return of a record", "<Html><Card><Button onClick={self.measure() into run} /><Text text={run.returns.return0} /></Card></Html>", ("returns.unnamed",), dependencies=(_UNNAMED,)),
    Check("an unnamed return of a latest task", "<Html><Text text={self.tasks.measure.returns.return0} /></Html>", ("returns.unnamed",), dependencies=(_UNNAMED,)),
    Check("an unnamed return of the task", "<Html><Text text={task.returns.return1} /></Html>", ("returns.unnamed",), kind="task"),
    Check("a named return", "<Html><Text text={deps.robot.tasks.move_to.returns.arrived} /></Html>", (), kind="custom", dependencies=(_ROBOT,)),
    Check("coalesce reads its operands", "<Html><Text text={nothing ?? args.x} /></Html>", ("roots.unknown",)),
    Check("spectral roles are scene colours", '<Space room="room:lab"><Placed dep="robot"><Glow on={task.running} color="spectrum-green" /></Placed><Route through={["robot"]} color="spectrum-violet-soft" /></Space>', (), kind="action", context="SCENE", dependencies=(_ROBOT,)),
    Check("spectral roles are classes", '<Html><Badge className="bg-spectrum-red-soft text-spectrum-red border-spectrum-cyan/40" /></Html>', ()),
    Check("spectral pens paint a logo", '<AgentLogo><Path d="M0 0" fill="spectrum-violet" stroke="spectrum-orange" /></AgentLogo>', (), context="LOGO"),
    Check("scene colours are roles", '<Space room="room:lab"><Placed dep="robot"><Glow on={task.running} color="info" /></Placed><Route through={["robot"]} color="chart-2" /></Space>', (), kind="action", context="SCENE", dependencies=(_ROBOT,)),
)


# --------------------------------------------------------------------------- building


def _tree(node: ComponentNodeInput) -> JSON:
    dumped: JSON = node.model_dump(mode="json", exclude_none=True)
    return dumped


def _scope(check: Check) -> dict[str, JSON]:
    return {
        "kind": check.kind,
        "context": check.context,
        "dependencies": [dict(dependency) for dependency in check.dependencies],
        "locals": [dict(local) for local in check.locals],
        "queries": [dict(query) for query in check.queries],
        "subscriptions": [dict(subscription) for subscription in check.subscriptions],
        "args": list(check.args) if check.args is not None else None,
        "catalog": check.catalog,
        "target": check.target,
    }


def _view(check: Check) -> BlokView | None:
    if not check.catalog:
        return None
    return blok_view(check.target) if check.target is not None else default_view()


def _parse(check: Check) -> ComponentNodeInput:
    """The case's tree, parsed with the view of the catalog it is written for."""
    return parse_bsx(check.source, view=blok_view(check.target)) if check.target is not None else parse_bsx(check.source)


def _check_scope(check: Check) -> "BlokScope":
    from arkitekt_spec.declare.blok.validate import BlokScope

    return BlokScope(
        kind=check.kind,
        context=check.context,
        dependencies=tuple(AgentDependencyInput.model_validate(dict(d)) for d in check.dependencies),
        locals=tuple(BlokLocalInput.model_validate(dict(local)) for local in check.locals),
        queries=tuple(BlokQueryInput.model_validate(dict(q)) for q in check.queries),
        subscriptions=tuple(BlokSubscriptionInput.model_validate(dict(s)) for s in check.subscriptions),
        args=check.args,
    )


def resolved(check: Check) -> dict[str, JSON]:
    """What the scope resolves to before it is checked: every local's kind
    (:func:`~arkitekt_spec.declare.blok.infer.resolve_scope`)."""
    from arkitekt_spec.declare.blok.infer import resolve_scope

    scope = resolve_scope(_parse(check), _check_scope(check), _view(check))
    locals_: list[JSON] = [local.model_dump(mode="json", exclude_none=True) for local in scope.locals]
    return {"locals": locals_}


def language_resolved(check: Check) -> dict[str, JSON]:
    """What the scope resolves to with no catalog at all: what a server stores as a blok's
    resolved locals. A local only a control's ``bind`` would type stays ``ANY``."""
    from arkitekt_spec.declare.blok.infer import resolve_scope

    scope = resolve_scope(_parse(check), _check_scope(check), None)
    locals_: list[JSON] = [local.model_dump(mode="json", exclude_none=True) for local in scope.locals]
    return {"locals": locals_}


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
            "scope": _scope(check),
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
