"""FastAPI server for the SRE On-Call Agent."""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

# Load app.env (sibling of this package's parent) before importing config.
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BASE, "app.env"))
load_dotenv()  # also pick up a cwd .env if present

from ddtrace.llmobs import LLMObs  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402

from app import agent, memory, triage  # noqa: E402
from app.config import Config  # noqa: E402
from app.router import ModelRouter  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

config = Config.from_env()
router = ModelRouter(config)
history: list[dict] = []  # single-session in-memory conversation (demo)

templates = Jinja2Templates(directory=os.path.join(_BASE, "templates"))
app = FastAPI(title="SRE On-Call Agent")


@app.on_event("startup")
async def _startup() -> None:
    LLMObs.enable(
        ml_app=config.dd_llmobs_ml_app,
        api_key=config.dd_api_key,
        site=config.dd_site,
        agentless_enabled=True,
    )
    logger.info("LLMObs enabled (ml_app=%s, site=%s)", config.dd_llmobs_ml_app, config.dd_site)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "dd_host": "app." + config.dd_site,
            "ml_app": config.dd_llmobs_ml_app,
            "routing": router.get_routing_table(),
        },
    )


@app.post("/chat")
async def chat(request: Request):
    body = await request.json()
    message = (body.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "empty message"}, status_code=400)
    try:
        result = agent.run_turn(message, history, config, router)
    except Exception as exc:
        logger.exception("turn failed")
        return JSONResponse({"error": str(exc)}, status_code=500)
    finally:
        LLMObs.flush()
    return JSONResponse(
        {
            "answer": result.display_text,
            "kind": result.kind,
            "withheld": result.withheld,
            "eval_score": result.eval_score,
            "model": result.model,
            "domain": result.domain,
            "cost_usd": result.cost_usd,
            "latency_ms": result.latency_ms,
            "bedrock_calls": result.bedrock_calls,
            "tools_used": result.tools_used,
            "guardrail_blocked": result.guardrail_blocked,
            "routing": result.routing_table,
            "recalled": result.recalled,
        }
    )


@app.post("/reset")
async def reset():
    history.clear()
    return {"ok": True}


@app.post("/triage")
async def triage_endpoint(request: Request):
    """Datadog monitor webhook → auto-triage → Slack. Point C."""
    if config.triage_token:
        if request.headers.get("X-Triage-Token") != config.triage_token:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    payload = await request.json()
    try:
        alert, result, slacked = triage.triage(payload, config, router)
    except Exception as exc:
        logger.exception("triage failed")
        return JSONResponse({"error": str(exc)}, status_code=500)
    finally:
        LLMObs.flush()
    return JSONResponse({
        "alert": alert["title"],
        "withheld": result.withheld,
        "eval_score": result.eval_score,
        "domain": result.domain,
        "model": result.model,
        "answer": result.display_text,
        "slack_posted": slacked,
    })


@app.get("/memory.md", response_class=PlainTextResponse)
async def ops_memory_raw():
    """Raw markdown of the Ops Memory Wiki."""
    return memory.read_all()


@app.get("/memory", response_class=HTMLResponse)
async def ops_memory():
    """The compounding Ops Memory Wiki, rendered."""
    import json as _json

    md = _json.dumps(memory.read_all())
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Ops Memory · CodeCraft</title>
<link rel=preconnect href=https://fonts.googleapis.com>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel=stylesheet>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
  body{{margin:0;background:#070a0e;color:#eaeef4;font-family:'IBM Plex Sans',sans-serif;line-height:1.6}}
  .wrap{{max-width:760px;margin:0 auto;padding:40px 24px 80px}}
  a.back{{color:#8b94a4;text-decoration:none;font-family:'IBM Plex Mono',monospace;font-size:12px}}
  a.back:hover{{color:#fff}}
  h1{{font-size:20px;letter-spacing:-.2px}} h2{{font-size:14px;margin-top:26px;color:#fff;border-top:1px solid rgba(255,255,255,.1);padding-top:18px}}
  ul{{padding-left:18px}} li{{margin:3px 0;color:#abacb4;font-size:13.5px}}
  code{{font-family:'IBM Plex Mono',monospace}}
  p{{color:#abacb4}}
</style></head>
<body><div class=wrap><a class=back href="/">&larr; console</a><div id=doc></div></div>
<script>document.getElementById('doc').innerHTML=marked.parse({md});</script>
</body></html>"""


@app.get("/healthz", response_class=PlainTextResponse)
async def healthz():
    return "ok"


if __name__ == "__main__":
    import uvicorn

    LLMObs.enable(
        ml_app=config.dd_llmobs_ml_app, api_key=config.dd_api_key,
        site=config.dd_site, agentless_enabled=True,
    )
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
