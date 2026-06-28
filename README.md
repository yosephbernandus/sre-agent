# SRE On-Call Agent

A self-tuning, observable SRE on-call assistant powered by AWS Bedrock (Nova) and
Datadog MCP. The agent investigates incidents using live telemetry data, guards
against hallucinations with an LLM-judge firewall, and can analyze its own
performance to recommend routing improvements.

## Features

- **Incident Investigation** - Agentic tool-use loop queries Datadog logs, metrics,
  and monitors via MCP for grounded answers.
- **Hallucination Firewall** - Every response is evaluated for grounding quality;
  ungrounded answers are withheld and escalated.
- **Bedrock Guardrails** - Input filtering for PII and prompt injection.
- **Model Router** - Routes incident domains (db, network, app, infra) to the
  optimal Bedrock model.
- **Self-Tune** - Reads its own observability metrics and recommends routing changes.
- **Full Observability** - Every interaction traced in Datadog LLM Obs with custom
  metrics (cost, latency, calls, hallucination rate).

## Quick Start

### 1. Install dependencies

```bash
cd sre-agent
pip install -r requirements.txt
```

### 2. Configure environment

Copy the template and fill in your credentials:

```bash
cp app.env.template app.env
# Edit app.env with your AWS and Datadog keys
```

### 3. Run the server

```bash
# local dev (convenience entrypoint)
python -m app.server          # reads app.env, enables LLM Obs, serves on $PORT (default 8080)

# or the canonical ASGI invocation (what production uses):
uvicorn app.server:app --host 0.0.0.0 --port 8080 --reload
```

The chat UI is available at `http://localhost:8080/`. In production the systemd
unit runs `uvicorn ... --workers 1 --proxy-headers` (1 worker is required - state
is in-memory; see `deploy/DEPLOY.md`).

## API Endpoints

| Method | Path       | Description                    |
|--------|------------|--------------------------------|
| GET    | `/`        | Chat UI (HTML)                 |
| POST   | `/chat`    | Send message, get AI response  |
| POST   | `/reset`   | Clear conversation history     |
| GET    | `/healthz` | Health check                   |

### POST /chat

Request:
```json
{"message": "What's causing the high error rate on the payments service?"}
```

Response:
```json
{
  "answer": "SEVERITY: P1\nMOST LIKELY ROOT CAUSE: connection pool exhausted...",
  "kind": "answer",
  "withheld": false,
  "eval_score": 0.8,
  "model": "us.amazon.nova-micro-v1:0",
  "domain": "db",
  "cost_usd": 0.000248,
  "latency_ms": 6943,
  "bedrock_calls": 4,
  "tools_used": ["search_datadog_logs", "search_datadog_metrics", "search_datadog_monitors"],
  "guardrail_blocked": false,
  "routing": {"db": "us.amazon.nova-micro-v1:0", "...": "..."}
}
```
`kind` is one of `answer | self_tune | control | blocked`. Withheld answers
(firewall) return `withheld: true`; blocked input (guardrail) returns `kind: "blocked"`.

## Project Structure

```
sre-agent/
├── app/
│   ├── __init__.py
│   ├── config.py          # Configuration from env vars
│   ├── server.py          # FastAPI routes
│   ├── agent.py           # Bedrock converse loop
│   ├── mcp_client.py      # Datadog MCP JSON-RPC client
│   ├── firewall.py        # LLM-judge grounding eval
│   ├── guardrails.py      # Bedrock guardrails + fallback
│   ├── router.py          # Domain → model routing
│   ├── self_tune.py       # Self-analysis workflow
│   ├── metrics.py         # Custom metric emission
│   └── state.py           # Shared in-memory turn log
├── templates/
│   └── index.html         # Chat UI (ops console)
├── scripts/
│   ├── seed_telemetry.py  # Seed Datadog with demo data
│   ├── create_dashboard.py
│   ├── create_monitors.py
│   └── smoke_test.py      # End-to-end scenario test (real Bedrock+DD)
├── tests/
│   └── test_logic.py      # Fast unit tests (no network)
├── terraform/
│   ├── dashboard.tf       # CodeCraft dashboard as code (us5)
│   └── variables.tf
├── deploy/
│   ├── DEPLOY.md          # EC2 deployment runbook
│   ├── codecraft.service  # systemd unit
│   └── deploy.sh          # one-shot ship + restart
├── requirements.txt
├── app.env.template
├── .gitignore
└── README.md
```

## Testing

```bash
python tests/test_logic.py     # fast unit tests (no Bedrock, no cost)
python scripts/smoke_test.py   # end-to-end scenarios (real Bedrock + Datadog, ~cents)
```

## Environment Variables

See `app.env.template` for the full list. Key variables:

| Variable                  | Required | Default                          |
|---------------------------|----------|----------------------------------|
| `AWS_ACCESS_KEY_ID`       | Yes      | -                                |
| `AWS_SECRET_ACCESS_KEY`   | Yes      | -                                |
| `AWS_REGION`              | No       | `us-east-1`                      |
| `BEDROCK_MODEL_DEFAULT`   | No       | `us.amazon.nova-micro-v1:0`      |
| `DD_API_KEY`              | Yes      | -                                |
| `DD_APP_KEY`              | Yes      | -                                |
| `DD_SITE`                 | No       | `us5.datadoghq.com`              |
| `DD_MCP_URL`              | No       | MCP endpoint for us5             |
| `DD_LLMOBS_ML_APP`        | No       | `sre-oncall-agent`               |
| `MAX_ITERATIONS`          | No       | `10`                             |
| `FIREWALL_THRESHOLD`      | No       | `0.5`                            |

## Deployment

The agent runs on an EC2 t3.small instance (Amazon Linux 2023), served by systemd
on port 80. See [`deploy/DEPLOY.md`](deploy/DEPLOY.md) for the full runbook
(launch → ship → provision → teardown). Update a running box with:

```bash
# from sre-agent/ ; PEM placed by deploy/DEPLOY.md Step 0
PEM=deploy/ft-oncall.pem IP=<public-dns> bash deploy/deploy.sh
```

**Notes**
- `app.env` uses plain `KEY=value` (no `export`, no inline `#`); it's read by
  `load_dotenv` at startup.
- No EC2 instance role (`iam:PassRole` denied) - the box uses AWS keys from `app.env`.
- No HTTPS without a domain (CloudFront/ECS denied on the hackathon account); use the
  EC2 public DNS over http for the demo.
