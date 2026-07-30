# DRAFT — OpenTelemetry Registry entry (NOT YET SUBMITTED)

> **This is an unsent draft.** The OpenTelemetry Registry
> (<https://opentelemetry.io/ecosystem/registry/>) is populated by PRs to
> [`open-telemetry/opentelemetry.io`][repo] that add a YAML file under
> `data/registry/`. Nothing here has been submitted. The project author submits
> it **manually, after the hackathon**, once the repo is public and the schema
> is re-checked against a current registry entry. Fill in `<REPO_URL>` /
> `<LICENSE>` before submitting.

[repo]: https://github.com/open-telemetry/opentelemetry.io

---

## Framing (read first)

The registry classifies entries by **component type** (exporter, instrumentation,
receiver, etc.). Home APM **would be registered as** an **exporter** —
specifically, a trace exporter that turns Home Assistant automation runs into
OTLP spans. It is **not** framed as "a sidecar" or "a tool"; the registry
describes the OTel *component* it provides, which is an exporter of trace data
for a system that ships none in core (as of Home Assistant 2026.7, verified
against the integrations list, July 2026). (Mechanically it is deployed as a
sidecar process, but that is a packaging detail, not the component
classification.)

- **Registry type:** `exporter`
- **Language:** `python`
- **Signal:** `traces` (primary). Metrics/logs are emitted too but the
  registry-classified contribution is the trace exporter.

## Proposed YAML (`data/registry/exporter-python-home-apm.yml`)

```yaml
title: Home APM — Home Assistant automation trace exporter
registryType: exporter
isNative: false
isFirstParty: false
language: python
tags:
  - traces
  - home-assistant
  - otlp
  - automation
license: <LICENSE>            # e.g. Apache-2.0 / MIT — set before submitting
description: >
  Exports Home Assistant automation and script runs as OpenTelemetry span
  trees. Subscribes to the Home Assistant WebSocket API, pulls each run's
  native trace record, reconstructs it into a parent/child span tree using the
  engine's real per-element start timestamps, and exports the spans over OTLP
  (HTTP/protobuf). Turns Home Assistant's otherwise cryptic node-path traces
  into standard flame graphs in any OTLP backend. As of Home Assistant 2026.7,
  core ships no OTLP trace exporter (verified against the integrations list,
  July 2026); this provides one out of tree.
authors:
  - name: <AUTHOR NAME>
    url: <AUTHOR URL>
urls:
  repo: <REPO_URL>           # public repo URL — set before submitting
```

## Notes for the submitter (do not include in the PR)

- **Honesty guardrails carried over:**
  - Describe it as reconstructing spans from **real per-element start
    timestamps**. Do **not** claim step *end* times are measured — HA stores no
    per-step end; ends are inferred as next-in-scope start (correctly-scoped
    inference). Keep the description to "real per-element start timestamps," which
    is exactly true.
  - Frame as **exporter component**, never "a sidecar" (packaging) and never
    "native" (`isNative: false`, `isFirstParty: false` are correct — this is
    out-of-tree, not part of HA or the OTel project).
  - Do **not** imply an official Home Assistant or OpenTelemetry endorsement.
- **Schema check before submitting:** copy the exact field set from a current
  `data/registry/exporter-*.yml` in the repo — the registry schema evolves; the
  YAML above mirrors the common fields (`title`, `registryType`, `isNative`,
  `language`, `tags`, `license`, `description`, `authors`, `urls`) but confirm
  against a live entry.
- **License / repo URL** are placeholders until the repo is public.
- Cross-reference: the companion upstream artifact is the Home Assistant
  architecture issue proposing *native* export
  (`ha-architecture-issue-DRAFT.md`). The registry entry is the out-of-tree
  exporter that exists today; the architecture issue is the in-core future.

---

_Draft prepared as part of the Home APM project (Agents of SigNoz, Track 3).
Author submits the PR manually post-submission._
