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

from app import agent  # noqa: E402
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
        }
    )


@app.post("/reset")
async def reset():
    history.clear()
    return {"ok": True}


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
