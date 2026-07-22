# Home APM — Home Assistant add-on (repository structure)

This directory is a **Home Assistant add-on repository layout** for shipping the
Home APM sidecar as a Supervisor add-on, so a HAOS / Supervised user could add
this repo under **Settings → Add-ons → Add-on Store → ⋮ → Repositories** and
install the sidecar from the UI.

## ⚠️ UNTESTED ON HAOS / SUPERVISOR — read this first

**This add-on has never been installed or run on a real Home Assistant OS /
Supervised system.** Supervisor add-ons only run under HAOS or a Supervised
install; the development machine for this project is Home Assistant in **plain
Docker on Windows 11 / Docker Desktop**, which has **no Supervisor** and
therefore **cannot install or test add-ons at all.**

Concretely, this means:

- The `config.yaml` schema, the base-image `build.yaml` wiring, and the
  Supervisor lifecycle (options → environment, ingress, watchdog) are written to
  the documented add-on spec but **not verified against a running Supervisor.**
- There is **no claim of a "tested one-click install."** The verified,
  demonstrated install path for this project is the Docker Compose / `foundryctl`
  cast documented in `deploy/` — not this add-on.
- Before any real use, this must be tested end-to-end on an actual HAOS box
  (add repository → install → set options → start → confirm traces reach SigNoz).
  Known-likely adjustments after that test: base-image tags per architecture,
  the exact options→env mapping, and whether the sidecar should talk to the
  Supervisor API for the token instead of a pasted long-lived token.

The value of shipping this **structure** (not a tested binary) is that it makes
the distribution story concrete and reviewable, and it is a ready starting point
for the post-submission HAOS test — nothing here overstates what was verified.

## Layout

```
addon/
├── README.md            ← this file (untested-on-HAOS notice)
├── repository.yaml      ← add-on repository manifest (name/url/maintainer)
└── home-apm/            ← the add-on itself
    ├── config.yaml      ← add-on manifest: options, schema, ports, arch
    ├── Dockerfile       ← builds FROM HA's base image (BUILD_FROM); installs homeapm
    ├── run.sh           ← bashio entrypoint: options → env vars → python -m homeapm
    ├── DOCS.md          ← the add-on's Documentation tab
    └── icon.png         ← PLACEHOLDER — not present; see "Icon" below
```

## Icon

Home Assistant renders `icon.png` (square, ~256×256) and optionally `logo.png`
in the add-on store card. **No image file is committed here** (this is a
structure artifact, and binary placeholders add no value). Before a real
release, drop a 256×256 PNG at `addon/home-apm/icon.png`. Until then the store
shows the default add-on placeholder icon — cosmetic only, does not affect
install.

## Relationship to `deploy/`

`deploy/` is the **tested** distribution path (Docker Compose fallback +
`foundryctl` casting, forge-verified). `addon/` is an **untested, HAOS-only**
alternative distribution for Supervisor users. They install the same sidecar
(`python -m homeapm`) from the same source; they differ only in packaging and in
how the HA token and OTLP endpoint are supplied.
