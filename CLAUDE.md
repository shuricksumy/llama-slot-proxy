# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A tiny single-file (`app.py`) reverse proxy in front of a shared `llama-server` (llama.cpp) instance. It pins each named "agent" (caller) to a fixed `id_slot`, so multiple clients sharing one llama-server keep separate warm prompt caches instead of fighting over llama-server's automatic slot routing.

Everything lives in `app.py` (~120 lines, aiohttp-based). There is no test suite, no build step, and no package structure — treat it as a whole when making changes.

## Commands

Run locally (needs `config.yaml` — copy from `config.example.yaml` first):
```bash
cp config.example.yaml config.yaml   # edit llama_url and agents
pip install -r requirements.txt
python app.py
```

Run via Docker:
```bash
docker build -t llama-slot-proxy:latest .
docker run -d --name llama-slot-proxy -p 8090:8090 \
  -v $(pwd)/config.yaml:/config/config.yaml:ro llama-slot-proxy:latest
# or
docker compose up -d --build
```

There are no lint/test/typecheck commands configured in this repo.

## Architecture

Request flow (`app.py`):
1. Route `/{agent}{tail:/.*}` matches any path under an agent name, e.g. `/router/v1/chat/completions`.
2. `agent` is looked up in the `AGENTS` dict (loaded from config); unknown agents get a 404 listing valid agent names.
3. The JSON request body is parsed, `id_slot` is force-set from the agent's config (overwriting anything the caller sent — this is the core mechanism), then any `extra_params` from the agent config are applied via `setdefault` (caller-supplied values win).
4. The request is forwarded as-is (method, headers minus `Host`/`Content-Length`, rewritten JSON body) to `LLAMA_URL + tail`. The `Authorization` header passes through untouched.
5. The full upstream response is buffered and returned — **no streaming support** (`"stream": true` will not behave correctly; would need a rewrite to forward chunks live).

Config (`config.yaml`, gitignored — `config.example.yaml` is the template) is loaded once at startup into module-level globals (`CONFIG`, `LLAMA_URL`, `AGENTS`, `LISTEN_HOST`, `LISTEN_PORT`). `load_config()` calls `sys.exit(1)` on any missing/invalid required field (`llama_url`, non-empty `agents` mapping, each agent having `id_slot`) — this is intentional fail-fast behavior for startup, but note `POST /_reload` also calls `load_config()` and catches `SystemExit` to avoid killing the running process on a bad reload.

Other endpoints:
- `GET /_health` — returns configured agent names; used by the Dockerfile `HEALTHCHECK`.
- `POST /_reload` — re-reads `config.yaml` from `CONFIG_PATH` without restarting, so agents/slots can be added or changed live.

Env vars: `CONFIG_PATH` (default `/config/config.yaml`), `LOG_LEVEL` (default `INFO`).

There is intentionally no request queuing or rate limiting — concurrency is whatever llama-server does with `--parallel`.