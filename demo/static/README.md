---
title: Home APM — Live Trace Demo
emoji: 🏠
colorFrom: blue
colorTo: green
sdk: static
app_file: index.html
pinned: false
license: mit
---

# Home APM — self-contained live demo

The full [Home APM](https://github.com/wiz-abhi/Home-APM) project bridges Home
Assistant automation traces into **SigNoz**. This Space is the one piece that
stands alone: the **real recorded** `trace/get` payloads are run through the
**actual** pure reconstruction (`trace_reconstruct.reconstruct` — the same
function 68 golden tests cover), and the resulting span tree is rendered as an
interactive **flame graph**, with a plain-English "ask your house" over the spans.
No SigNoz, no Home Assistant, no API key.
