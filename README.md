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

In n8n, this is just the `baseURL` field on each `OpenAI Chat Model`
node - e.g. `http://192.168.111.111:8090/router/v1`. Nothing else
about the node or the Agent changes. Your existing API key credential
still works unchanged, since the proxy forwards the `Authorization`
header through as-is.

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
