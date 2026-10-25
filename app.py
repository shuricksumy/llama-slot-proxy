import os
import sys
import asyncio
import json
import logging
import yaml
from aiohttp import web, ClientSession, ClientTimeout

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("llama-slot-proxy")

CONFIG_PATH = os.environ.get("CONFIG_PATH", "/config/config.yaml")
DEFAULT_UPSTREAM_TIMEOUT = 1800


def is_valid_timeout(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def load_config(path):
    if not os.path.isfile(path):
        log.error(f"Config file not found at {path}")
        sys.exit(1)
    with open(path, "r") as f:
        cfg = yaml.safe_load(f) or {}

    if "llama_url" not in cfg:
        log.error("Config missing required top-level key: llama_url")
        sys.exit(1)
    if not isinstance(cfg["llama_url"], str):
        log.error("Config key llama_url must be a string")
        sys.exit(1)
    if "agents" not in cfg or not isinstance(cfg["agents"], dict) or not cfg["agents"]:
        log.error("Config missing required top-level key: agents (must be a non-empty mapping)")
        sys.exit(1)

    # UPSTREAM_TIMEOUT env var overrides the config file value
    env_timeout = os.environ.get("UPSTREAM_TIMEOUT")
    if env_timeout:
        try:
            cfg["upstream_timeout"] = float(env_timeout)
        except ValueError:
            log.error(f"UPSTREAM_TIMEOUT env var must be a number, got '{env_timeout}'")
            sys.exit(1)
    cfg.setdefault("upstream_timeout", DEFAULT_UPSTREAM_TIMEOUT)
    if not is_valid_timeout(cfg["upstream_timeout"]):
        log.error("Config key upstream_timeout must be a positive number (seconds)")
        sys.exit(1)

    for name, agent_cfg in cfg["agents"].items():
        if "id_slot" not in agent_cfg:
            log.error(f"Agent '{name}' is missing required field: id_slot")
            sys.exit(1)
        if "llama_url" in agent_cfg and not isinstance(agent_cfg["llama_url"], str):
            log.error(f"Agent '{name}' llama_url must be a string if provided")
            sys.exit(1)
        if "upstream_timeout" in agent_cfg and not is_valid_timeout(agent_cfg["upstream_timeout"]):
            log.error(f"Agent '{name}' upstream_timeout must be a positive number (seconds) if provided")
            sys.exit(1)

    return cfg


CONFIG = load_config(CONFIG_PATH)
LLAMA_URL = CONFIG["llama_url"].rstrip("/")
AGENTS = CONFIG["agents"]
UPSTREAM_TIMEOUT = CONFIG["upstream_timeout"]
LISTEN_HOST = CONFIG.get("listen_host", "0.0.0.0")
LISTEN_PORT = int(CONFIG.get("listen_port", 8090))

log.info(f"Loaded config from {CONFIG_PATH}")
log.info(f"Forwarding to llama.cpp at {LLAMA_URL}")
log.info(f"Upstream timeout: {UPSTREAM_TIMEOUT:g}s")
for name, agent_cfg in AGENTS.items():
    agent_url = agent_cfg.get("llama_url", LLAMA_URL)
    agent_timeout = agent_cfg.get("upstream_timeout", UPSTREAM_TIMEOUT)
    log.info(
        f"  agent '{name}' -> id_slot {agent_cfg['id_slot']}, upstream {agent_url}, "
        f"timeout {agent_timeout:g}s"
    )


async def health(request):
    return web.json_response({"status": "ok", "agents": list(AGENTS.keys())})


async def reload_config(request):
    """POST /_reload - re-read the config file without restarting the container."""
    global CONFIG, LLAMA_URL, AGENTS, UPSTREAM_TIMEOUT
    try:
        CONFIG = load_config(CONFIG_PATH)
    except SystemExit:
        return web.json_response({"error": "failed to reload config, check logs"}, status=500)
    LLAMA_URL = CONFIG["llama_url"].rstrip("/")
    AGENTS = CONFIG["agents"]
    UPSTREAM_TIMEOUT = CONFIG["upstream_timeout"]
    log.info(f"Config reloaded (upstream timeout {UPSTREAM_TIMEOUT:g}s)")
    return web.json_response({"status": "reloaded", "agents": list(AGENTS.keys())})


async def proxy(request):
    agent = request.match_info["agent"]
    agent_cfg = AGENTS.get(agent)
    if agent_cfg is None:
        return web.json_response(
            {"error": f"unknown agent '{agent}'", "known_agents": list(AGENTS.keys())},
            status=404,
        )

    raw_body = await request.read()
    try:
        payload = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        return web.json_response({"error": "request body is not valid JSON"}, status=400)

    # The actual fix: force this agent onto its own configured slot every time
    payload["id_slot"] = agent_cfg["id_slot"]

    # Allow optional per-agent overrides (e.g. force cache_prompt on)
    for key, value in agent_cfg.get("extra_params", {}).items():
        payload.setdefault(key, value)

    tail = request.match_info["tail"] or "/"
    base_url = agent_cfg.get("llama_url", LLAMA_URL).rstrip("/")
    target_url = f"{base_url}{tail}"
    timeout = agent_cfg.get("upstream_timeout", UPSTREAM_TIMEOUT)

    forward_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length")
    }

    try:
        async with ClientSession(timeout=ClientTimeout(total=timeout, sock_read=timeout)) as session:
            async with session.request(
                request.method,
                target_url,
                json=payload,
                headers=forward_headers,
            ) as resp:
                data = await resp.read()
                return web.Response(
                    body=data,
                    status=resp.status,
                    content_type=resp.content_type,
                )
    except Exception as e:
        log.error(f"Error proxying request for agent '{agent}': {type(e).__name__}: {e}")
        status = 504 if isinstance(e, asyncio.TimeoutError) else 502
        return web.json_response(
            {"error": "upstream request failed", "detail": f"{type(e).__name__}: {e}"},
            status=status,
        )


app = web.Application()
app.router.add_get("/_health", health)
app.router.add_post("/_reload", reload_config)
app.router.add_route("*", "/{agent}{tail:/.*}", proxy)

if __name__ == "__main__":
    web.run_app(app, host=LISTEN_HOST, port=LISTEN_PORT)
