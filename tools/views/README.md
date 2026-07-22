# Home APM - SigNoz saved views + demo surfaces

Replicable, idempotent tooling for the SigNoz-native surfaces of the demo:
saved Trace/Logs Explorer **views** and the click-by-click **demo deep-links**.
Everything is defined as code and pushed over verified SigNoz REST APIs.

## Files

| File | What it does |
|------|--------------|
| `apply_views.py` | Builds + applies the 5 saved views (idempotent). Writes `views.json`. |
| `views.json` | The generated record of every applied view (for the repo / review). |
| `make_demo_links.py` | Generates `DEMO-LINKS.md` with fresh trace IDs. |
| `DEMO-LINKS.md` | Click-by-click URLs for the video, one per beat. |

## Saved views (`apply_views.py`)

Five views, matched-by-name idempotent:

| View | Source | Filter |
|------|--------|--------|
| **Automation runs** | traces | `service.name = ha.automation` AND `kind_string = Server` (root spans only) |
| **Slow automations (>5s)** | traces | root runs with `duration_nano > 5e9` |
| **Failed steps** | traces | `has_error = true` (the ERROR lands on the service-call child) |
| **3am mystery** | traces | `automation.name = 'Hallway Lights 3AM'`, root spans |
| **Home APM sidecar logs** | logs | `service.name = 'ha.sidecar'` - the sidecar's own OTLP logs (`INFO converted run ... -> trace ...`), **live** |

```bash
.venv/Scripts/python.exe tools/views/apply_views.py --verify
```

`--verify` GETs each view back and runs the equivalent `query_range` so you see
the live matching-row count per filter. `--dry-run` writes `views.json` only.

### Wire facts (verified against SigNoz v0.132.2 source + this instance)

- Endpoint: `GET/POST /api/v1/explorer/views`, `PUT/DELETE /api/v1/explorer/views/{id}`.
  List is scoped by `?sourcePage=traces|logs`.
- Body is a **v3 `SavedView`**: `compositeQuery` is the *v3* shape
  (`builderQueries` MAP keyed `"A"`, filters as `filters.items`), NOT the v5
  `builder.queryData` array the dashboard widgets use. `SavedView.Validate()`
  rejects anything else.
- Attribute-key shapes were read from this instance's autocomplete API so the
  Explorer re-hydrates the filter chips: `service.name` (resource, isColumn),
  `automation.name` (tag), `has_error`/`kind_string`/`duration_nano` (columns).
- Root automation-run spans are exactly `kind_string = 'Server'` (verified: 151
  Server roots, all with empty `parent_span_id`; 616 Internal = structural
  children parallel/repeat/wait/service_call).
- **Idempotency is delete-then-create, NOT PUT.** In v0.132.2 the `UpdateView`
  handler stores the composite query as a raw `[]byte` (bun escape-encodes it)
  instead of `string(data)` like the create path; that double-encodes the stored
  JSON and every later LIST 500s with
  `invalid character '\' looking for beginning of value`. Delete + create keeps
  the stored `data` column clean and is fully idempotent. *(Upstream bug worth a
  one-line PR: `implsavedview/module.go` UpdateView should pass `string(data)`.)*

### How opening a view applies the filter

SigNoz's saved-view open path is
`redirectWithQueryBuilderData(mapQueryDataFromApi(compositeQuery), ...)`
(`frontend/src/components/ExplorerCard/utils.ts`). `mapQueryDataFromApi` is the
standard v3->frontend converter, so the stored v3 `filters.items` hydrate the
query builder on click. Our payload is the exact shape SigNoz's own "Save view"
produces, so the views behave identically to natively-saved ones. Verified live:
all 4 trace views render by name in the **Views** tab and re-hydrate on open.

## Demo deep-links (`make_demo_links.py`)

```bash
.venv/Scripts/python.exe tools/views/make_demo_links.py
```

Regenerate right before recording - trace IDs age out of SigNoz's retention
window; the Explorer/dashboard/service-map links are stable. Explorer links use
the **frontend** `compositeQuery` URL shape with a populated `filter.expression`
(verified live to keep `dataSource=traces` and apply the filter on load - the
naive v3 `builderQueries`-map URL shape gets reset to a default query, so it is
deliberately not used for URLs).

## Service map (verified, no code change needed)

The house service map draws correctly from the frozen S0A semconv
(`span.kind` CLIENT children + `peer.service`). `POST /api/v1/dependency_graph`
returns 8 edges, e.g.:

```
ha.automation -> ha.light                   calls=199 err%=0
ha.automation -> ha.persistent_notification calls=44  err%=70  <- failing edge
ha.automation -> ha.cover / ha.climate / ha.input_number / ha.input_boolean
ha.light      -> ha.input_boolean
ha.cover      -> ha.input_boolean
```

All 7 `ha.*` services also show sane RED metrics on `POST /api/v1/services`.
No `src/` change required.
