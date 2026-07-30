# The adapter boundary — and what a second one would cost

Home APM converts one thing today: a Home Assistant `trace/get` payload into an
OpenTelemetry span tree. This document does **not** claim the sidecar already
supports other engines. It makes a narrower, honest claim: the conversion is
already isolated behind a documented function-shaped boundary, and a second
engine (n8n is the worked example) would be a *distinct, differently-shaped
adapter* — **not the same engine repointed at a new URL.** That distinction is
the whole point; the sections below spell out exactly where the seam is and
exactly why n8n does not slot into it for free.

> Status: **roadmap / prose only.** No n8n adapter is built, cast, or tested.
> Spec item #2, narrowed to what is actually true: "a documented adapter
> boundary, and what a second, differently-shaped adapter would cost."
> Generality is claimed in prose, not staked on a live second engine.

---

## 1. Where the boundary already is

The sidecar is split into three modules with one deliberately pure one in the
middle (`src/homeapm/`):

```
ws_client  ──(raw trace/get payload dict)──▶  trace_reconstruct  ──(list[SpanSpec])──▶  otlp_emit
  I/O                                          PURE, I/O-free                            I/O
```

`trace_reconstruct` is the adapter. It has no sockets, no clock reads, no
network, no global state — it is `payload dict in → list[SpanSpec] out`. That is
what makes it golden-testable offline (`clone && pytest`, no house) and it is
also what makes it a *boundary*: everything HA-specific lives on the input side
of that function, and everything OTLP/SigNoz-specific lives on the output side
(`otlp_emit`). Neither side knows about the other.

The two sides of the boundary:

- **Upstream of the seam (engine-specific):** how you obtain a run's execution
  record, and how that record is shaped. For HA this is the WebSocket
  `trace/get` call in `ws_client` and the **path-keyed nested dict** it returns.
- **Downstream of the seam (engine-agnostic):** `otlp_emit` mints
  `run_id → trace_id`, stamps CLIENT/SERVER `span.kind` so the service map
  draws, and ships OTLP to `:4318`. It consumes `SpanSpec` and nothing else. A
  second adapter that also produced `list[SpanSpec]` would reuse `otlp_emit`
  **unchanged.**

So the reusable asset is precisely the output contract (`SpanSpec` + `otlp_emit`),
not the reconstruction algorithm. The algorithm is HA-shaped and stays HA-shaped.

---

## 2. The exact interface (from the current code — unmodified)

The contract a new adapter must satisfy is the public signature of
`src/homeapm/trace_reconstruct.py`:

```python
def reconstruct(payload: dict[str, Any]) -> list[SpanSpec]:
    """A source engine's run record (dict) → a list of OTel spans."""
```

and the frozen output dataclass it returns (schema §0A — additive-only, never
rename a field; four downstream consumers filter/correlate on these names):

```python
@dataclass(frozen=True, slots=True)
class SpanSpec:
    # --- tree / timing ---
    span_id: str
    parent_span_id: str | None      # None only for the run root
    name: str
    kind: SpanKind                  # SERVER | INTERNAL | CLIENT (drives service map)
    service_name: str               # "ha.automation" for structure; "ha.<domain>" for calls
    start_unix_nano: int            # real per-element start (§0B)
    end_unix_nano: int              # inferred end; end >= start always holds

    # --- frozen §0A attributes ---
    automation_name: str
    automation_id: str
    automation_room: str
    node_path: str                  # e.g. "conditions/0/conditions/1"
    step_type: StepType             # trigger|condition|choose|sequence|wait|repeat|parallel|service_call
    context_id: str                 # feeds logs↔trace correlation (#13)
    run_id: str                     # sidecar owns run_id → trace_id
    result: Result                  # ok | error | skipped | timeout
    changed_variables: str = "{}"   # JSON string
    template_errors: str | None = None
    peer_service: str | None = None # target domain for CLIENT spans

    # --- otel status ---
    status_error: bool = False
    status_message: str | None = None

    # additive-only extension hook, never used to rename the above
    extra_attributes: dict[str, str] = field(default_factory=dict)
```

The invariants `reconstruct` guarantees (enforced by the golden tests, and which
any second adapter must also uphold for `otlp_emit` to accept its output):

- exactly one root span (`parent_span_id is None`);
- every non-root `parent_span_id` refers to a `span_id` present in the list;
- starts are monotonic in traversal order and every `end >= start`;
- every span carries a non-empty `automation_name` and `node_path`.

**Read this as the porting checklist.** A new engine is "in" the moment it can
emit a `list[SpanSpec]` honoring those four invariants. Everything downstream —
trace_id minting, service map, dashboards, `$room`, "ask your house", alerts —
*would need to work unchanged*, because they were only ever coupled to
`SpanSpec` — but that is **untested**: no second adapter exists yet, so the
claim is aspirational, not demonstrated.

### The one honest generalization the names would need

The field names are HA-flavored (`automation_name`, `automation_id`,
`automation_room`). Per the §0A freeze rule these must **never be renamed**
(renaming breaks four consumers at once). A real multi-engine refactor would
therefore keep the `automation_*` names as the stable wire schema and let a
second adapter map its own vocabulary onto them (n8n: workflow → `automation_name`,
workflow id → `automation_id`, no natural `room` → empty string), or add a
neutral alias additively via `extra_attributes` (e.g. `source.engine = "n8n"`,
`workflow.name = ...`). That is a schema-evolution decision, not a code change to
`reconstruct` — and it is exactly the kind of decision this doc exists to flag
before anyone writes a second adapter.

---

## 3. Why n8n is a *different-shaped* adapter, not a config change

The part worth stating plainly: **you cannot point `reconstruct` at n8n.** The
two engines record a run in structurally different ways, and almost all of the
HA-specific cleverness in `reconstruct` is spent undoing HA's specific shape.

| | Home Assistant `trace/get` | n8n `runData` (execution) |
|---|---|---|
| Record shape | **Path-keyed nested dict**: `node_path → list[TraceElement]` (e.g. `"action/0/choose/0/sequence/1"`) | **Flat map keyed by node name**: `runData[nodeName] → list[taskData]`, plus a separate `connections`/DAG describing edges |
| Structure encoded in… | the **string key itself** (`/`-separated path is the tree) | an **explicit graph** (`connections`) — the keys carry no hierarchy |
| Parent/child | derived by **segment-prefix matching** on paths (`_is_strict_prefix`, `_assign_parents`) | derived by **following DAG edges** between named nodes |
| Iterations | repeated element in the same path's list (`seq` index) | multiple `taskData` runs per node; loops are edges back in the DAG |
| Per-step timing | real per-element `timestamp`; **no end** → end inferred from next-in-scope start (§0B) | `startTime` + explicit `executionTime` per task — an **end is actually recorded** |
| Errors | `error` / `result.error` on the element; template errors special-cased | `taskData.error` object per node |
| Domains/services | `result.params.{domain,service}` → CLIENT span + `peer.service` | node `type` (e.g. `n8n-nodes-base.httpRequest`) → different service-naming scheme |

The consequences for the code:

1. **The tree builder is HA-shaped, top to bottom.** `_flatten_events`,
   `_assign_parents` (segment-prefix parenting), `_compute_ends`
   (next-in-scope-start end inference with a `parallel`-branch scope split),
   `_walk_config`, and `_branch_key` all operate on `/`-delimited node paths.
   n8n has **no node paths** — it has named nodes and an edge list. An n8n
   adapter would replace this entire middle with a **DAG walk** (topological
   traversal of `connections`, one span per `taskData`, parent = upstream node).
   That is a second function, `reconstruct_n8n`, sharing only the `SpanSpec`
   output type — *not* the same engine repointed.

2. **n8n actually records step ends**, so an n8n adapter would be *more* precise
   than HA on duration and would **not** need the §0B "no per-step end"
   inference at all. Its honesty story is different (and simpler); reusing HA's
   inference on it would be wrong.

3. **The classification tables are HA vocabulary** (`choose`, `repeat`,
   `parallel`, `wait_for_trigger`, `condition:`, `service_call`). n8n's control
   flow (IF, Switch, Merge, SplitInBatches/loops, sub-workflows) maps onto the
   *same* `StepType` enum only loosely; some n8n concepts have no HA analogue and
   vice-versa. Faithful mapping is a design task, not a rename.

So the honest generality claim is: **the output half of the pipeline
(`SpanSpec` + `otlp_emit` + every SigNoz consumer) is engine-agnostic and would
be reused verbatim; the input half (`reconstruct`) is engine-specific and a
second engine needs a second, differently-shaped implementation.** The value of
having built HA first is that the boundary and the output contract are already
proven — the expensive, ambiguous part (what does a good span tree for
home/workflow automation even look like?) is answered.

---

## 4. What an n8n adapter would concretely look like (sketch, unbuilt)

A realistic shape, to make the boundary tangible — **not committed, not built:**

```python
# hypothetical src/homeapm/adapters/n8n_reconstruct.py — DOES NOT EXIST
def reconstruct_n8n(execution: dict[str, Any]) -> list[SpanSpec]:
    """n8n execution JSON (runData + workflow connections) → list[SpanSpec].

    Differently shaped from HA's reconstruct(): walks the DAG in `connections`
    instead of prefix-matching path keys, and uses n8n's recorded per-task
    start+executionTime (so no §0B end inference is needed).
    """
    run_data   = execution["data"]["resultData"]["runData"]     # node -> [taskData]
    connections = execution["workflow"]["connections"]           # the DAG edges
    # 1. one _Event per taskData; parent = upstream node via `connections`
    # 2. start = taskData["startTime"]; end = start + taskData["executionTime"]
    # 3. StepType from node "type"; CLIENT/peer.service for HTTP/DB/API nodes
    # 4. map workflow.name -> automation_name, workflow.id -> automation_id,
    #    room -> "" (n8n has no room); tag extra_attributes["source.engine"]="n8n"
    # 5. emit SpanSpec honoring the four invariants -> otlp_emit unchanged
    ...
```

Everything after it — `otlp_emit`, the dashboard, `$room` (degrades gracefully to
"all" when room is empty), the service map, "ask your house", the alerts — is
untouched. That reuse is the payoff of the boundary — on paper, until a second
adapter actually exercises it. The DAG walk,
the timing model, and the vocabulary mapping are the genuinely new work, and they
are why this is a roadmap item and not a switch to flip.

---

See also: `src/homeapm/trace_reconstruct.py` (the boundary), `docs/DEMO-RUNBOOK.md`
(the live HA demo), and the blog "What's next" section (`docs/upstream/`).
