# Home APM — self-contained demo

**⚡ Live: https://wiz-abhi-home-apm-demo.static.hf.space**

The full project needs a 9-container stack (SigNoz + ClickHouse + Home Assistant
+ the sidecar). This demo is the one piece that stands alone: it runs the **real**
pure reconstruction (`trace_reconstruct.reconstruct` — the same function the 68
golden tests cover) on the **recorded** `trace/get` payloads in `fixtures/`, and
renders the resulting span tree as an interactive **flame graph**, with a
plain-English "ask your house" over the spans. No SigNoz, no Home Assistant, no key.

## Run it

```bash
# server version (any Docker host / your machine)
python demo/app.py                 # → http://localhost:7860

# or rebuild the static, server-free version (deployed to the HF Space)
python demo/build_static.py        # → demo/static/index.html
```

## Files

| Path | What |
|---|---|
| `app.py` | stdlib `http.server`: reconstructs the fixtures + serves the flame-graph UI and the `/api/ask` heuristic |
| `build_static.py` | bakes the real reconstruction output + the ask logic into a single static `index.html` |
| `static/index.html` | the deployed, server-free build (free HF **Static** Space) |
| `trace_reconstruct.py` | vendored verbatim from `src/homeapm/` — pure, stdlib-only |
| `fixtures/` | the four real recorded demo runs |
| `Dockerfile` | the server build (for a Docker host / HF PRO) |
