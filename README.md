# llama-slot-proxy

Tiny reverse proxy that pins each "agent" (or any caller) to a fixed
`id_slot` on a shared `llama-server` instance, so multiple n8n agents
(or anything else) sharing one llama.cpp server keep their own warm
prompt cache instead of fighting over automatic slot routing.

n8n (or any OpenAI-compatible client) is pointed at this proxy instead
of directly at llama-server. The proxy stamps `id_slot` onto the
request body based on the URL path, then forwards everything else
untouched (including your `Authorization` header) straight to
llama-server.

## Related project

[llama-service](https://github.com/shuricksumy/llama-service) is a
companion repo that runs the `llama-server` side of this setup: a
systemd-managed, Vulkan-backed `llama-server` with a vendored
self-updating engine, a preset-based model switcher, and the
`--parallel`/`--ctx-size`/`--cache-reuse`/etc. tuning this proxy
expects on the other end (see "0. Launch llama-server with matching
flags" below). Use it if you don't already have a `llama-server`
instance to point this proxy at.

## 0. Launch llama-server with matching flags

The goal of this whole setup: each agent gets its own slot with its own
persistent KV-cache, so a system prompt that's identical call-to-call
(e.g. an n8n agent's fixed instructions) is reused instead of being
reprocessed from scratch every time - only the new trailing tokens get
computed. That's the difference between "instant" and "reprocess a few
thousand tokens" on every single call.

For that to actually work, `llama-server` itself needs to be launched
with flags that give it enough slots and enough context to keep all of
them warm at once:

- **`--parallel N`** (`-np N`) - number of slots. Must be **at least**
  the number of agents in `config.yaml`, since each agent is pinned to
  its own `id_slot` (0-indexed). 4 agents in the example config above
  means `--parallel 4`.
- **`--ctx-size N`** (`-c N`) - total context window. When
  `--parallel` > 1, llama-server splits this evenly across slots, so
  each agent's usable context is roughly `ctx-size / parallel`. Size
  it generously enough that every agent's system prompt plus
  conversation history fits with room to spare - too small and the
  cache gets evicted/truncated and you lose the benefit of this whole
  proxy.
- **`--cache-reuse N`** - minimum chunk size (in tokens) llama-server
  will try to reuse from a slot's existing cache when the new prompt
  diverges partway through, instead of discarding the whole thing.
  Useful once the system-prompt prefix is warm but the trailing
  conversation changes on every call.
- **`--slot-save-path PATH`** - enables the `/slots` save/restore/erase
  endpoints on llama-server so a slot's KV-cache can be persisted to
  disk and restored after a restart, instead of every agent starting
  cold again.
- **`--defrag-thold N`** (`-dt N`) - KV-cache defragmentation
  threshold; matters more the longer you run several slots
  continuously.

At the request level (already wired up by this proxy, nothing to do
on your end):

- **`id_slot`** - stamped onto every request by the proxy based on
  the URL path. This is the actual mechanism that pins an agent to a
  fixed slot instead of letting llama-server auto-pick whichever slot
  is free.
- **`cache_prompt`** - set via `extra_params: { cache_prompt: true }`
  in `config.yaml` (see below), tells llama-server to reuse the
  slot's cached tokens instead of reprocessing the full prompt.

Flag names and defaults change between llama.cpp releases -
`llama-server --help` for your build is authoritative. `GET /slots`
on llama-server itself (not this proxy) is useful for inspecting live
cache state per slot while debugging.

## 1. Configure

Copy the example config and edit it:

```bash
cp config.example.yaml config.yaml
```

```yaml
llama_url: "http://192.168.111.111:18080"   # your real llama-server

agents:
  router:
    id_slot: 0
  recipe:
    id_slot: 1
  music:
    id_slot: 2
  spare:
    id_slot: 3
```

Each key under `agents` becomes a URL path segment. Add or remove
agents freely - no code changes needed, just edit the YAML and hit
`/_reload` (see below) or restart the container.

`extra_params` under an agent is optional - anything you put there
gets added to the outgoing request body *only if the caller didn't
already set it*. Useful for forcing `cache_prompt: true` etc. without
relying on every caller remembering to set it.

## 2. Get an image

Pull the pre-built image, published by the `docker-publish` GitHub Action
on every push to `main` and on version tags:

```bash
docker pull ghcr.io/shuricksumy/llama-slot-proxy:latest
```

Or build your own:

```bash
docker build -t llama-slot-proxy:latest .
```

No external dependencies beyond `aiohttp` and `PyYAML` - pure Python,
small image, builds in seconds.

## 3. Run it

Standalone:

```bash
docker run -d --name llama-slot-proxy \
  -p 8090:8090 \
  -v $(pwd)/config.yaml:/config/config.yaml:ro \
  llama-slot-proxy:latest
```

Or via the included `docker-compose.yml` (builds locally):

```bash
docker compose up -d --build
```

Or via `docker-compose.ghcr.yml`, using the pre-built image from GHCR
instead of building locally:

```bash
docker compose -f docker-compose.ghcr.yml up -d
```

## 4. Point your clients at it

Instead of:
```
http://192.168.111.111:18080/v1/chat/completions
```

use:
```
http://<proxy-host>:8090/router/v1/chat/completions
http://<proxy-host>:8090/recipe/v1/chat/completions
http://<proxy-host>:8090/music/v1/chat/completions
```

### n8n usage example

This proxy was built for exactly this setup: several
[n8n](https://n8n.io/) AI Agent workflows sharing one local
`llama-server` instance, each wanting its own warm system-prompt
cache instead of thrashing a single shared slot.

In n8n, pointing an agent at the proxy is just the `Base URL` field
on its `OpenAI Chat Model` node (see the
[n8n docs](https://docs.n8n.io/) for that node and the AI Agent node
it feeds) - e.g. `http://192.168.111.111:8090/router/v1`. Nothing
else about the node or the Agent changes. Your existing API key
credential still works unchanged, since the proxy forwards the
`Authorization` header through as-is. Give each n8n workflow/agent
its own path (`/router`, `/recipe`, `/music`, ...) matching an entry
in `config.yaml`, and each keeps its own dedicated `id_slot` and
cache no matter how many other agents are hammering the same
llama-server at the same time.

## 5. Reload config without restarting

```bash
curl -X POST http://<proxy-host>:8090/_reload
```

With auth (if your setup requires a Bearer token to reach the proxy):

```bash
curl -X POST http://<proxy-host>:8090/_reload \
  -H "Authorization: Bearer <your-api-key>"
```

Picks up changes to `config.yaml` (new agents, changed slot mappings)
without dropping the container or losing in-flight requests.

## 6. Health check

```bash
curl http://<proxy-host>:8090/_health
```

With auth:

```bash
curl http://<proxy-host>:8090/_health \
  -H "Authorization: Bearer <your-api-key>"
```

Returns the list of currently configured agent names. Also used by
the Dockerfile's built-in `HEALTHCHECK`.

## Verifying it's actually working

Send the same system prompt twice through the same agent path, with a
different final user message each time, and compare
`usage.prompt_tokens_details.cached_tokens` in the two responses - it
should be `0` on the first call and roughly equal to the shared
prefix's token count on the second. See the project chat history this
was built from for a full worked example.

## Known limitations

- **No streaming support.** The proxy buffers the full upstream
  response before replying, so `"stream": true` requests will not
  work correctly. Fine for typical n8n Agent usage (non-streaming),
  but worth knowing if you ever need token-by-token streaming through
  this proxy - it would need a rewrite to forward chunks live.
- **No request queuing/rate limiting.** It's a thin passthrough - all
  concurrency behavior is whatever llama-server itself does with
  `--parallel`.
