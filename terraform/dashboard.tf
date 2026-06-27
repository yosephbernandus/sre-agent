terraform {
  required_providers {
    datadog = { source = "DataDog/datadog" }
  }
}

provider "datadog" {
  api_key = var.datadog_api_key
  app_key = var.datadog_app_key
  api_url = var.datadog_api_url
}

# CodeCraft — Observable SRE Agent dashboard.
# Combines: custom sre_agent.* metrics, Datadog LLM Obs built-ins (ml_obs.*),
# and agent/MCP trace metrics. Scoped by the $ml_app template variable where the
# metric carries an ml_app tag.
resource "datadog_dashboard" "codecraft_sre" {
  title       = "CodeCraft — Observable SRE Agent"
  description = "Self-tuning observable SRE on-call agent: cost, tokens, latency, MCP tool calls, grounding/firewall, guardrails, errors."
  layout_type = "ordered"

  template_variable {
    name     = "ml_app"
    prefix   = "ml_app"
    defaults = [var.ml_app]
  }

  # ── Row 1: headline numbers ──────────────────────────────────────────
  widget {
    query_value_definition {
      title = "Est. Bedrock cost (USD)"
      request {
        q          = "sum:sre_agent.bedrock.cost_usd{*}"
        aggregator = "sum"
      }
    }
  }
  widget {
    query_value_definition {
      title     = "LLM total tokens"
      autoscale = true
      request {
        q          = "sum:ml_obs.span.llm.total.tokens{$ml_app}.as_count()"
        aggregator = "sum"
      }
    }
  }
  widget {
    query_value_definition {
      title     = "Answers withheld (firewall)"
      autoscale = true
      request {
        q          = "sum:sre_agent.hallucination{*}.as_count()"
        aggregator = "sum"
      }
      custom_link {
        label = "LLM traces"
        link  = "https://app.us5.datadoghq.com/llm/traces"
      }
    }
  }
  widget {
    query_value_definition {
      title     = "Guardrail blocks"
      autoscale = true
      request {
        q          = "sum:sre_agent.guardrail_blocks{*}.as_count()"
        aggregator = "sum"
      }
    }
  }

  # ── Row 2: cost & tokens ─────────────────────────────────────────────
  widget {
    timeseries_definition {
      title = "Cost per request by model (USD)"
      request {
        q            = "avg:sre_agent.bedrock.cost_usd{*} by {model}"
        display_type = "line"
      }
    }
  }
  widget {
    timeseries_definition {
      title = "Token usage (input vs output)"
      request {
        q            = "sum:ml_obs.span.llm.input.tokens{$ml_app}.as_count()"
        display_type = "bars"
      }
      request {
        q            = "sum:ml_obs.span.llm.output.tokens{$ml_app}.as_count()"
        display_type = "bars"
      }
    }
  }

  # ── Row 3: performance & tool calls ──────────────────────────────────
  widget {
    timeseries_definition {
      title = "Agent latency by model (ms)"
      request {
        q            = "avg:sre_agent.bedrock.latency_ms{*} by {model}"
        display_type = "line"
      }
    }
  }
  widget {
    timeseries_definition {
      title = "LLM trace duration p95 (s)"
      request {
        q            = "p95:ml_obs.trace.duration{$ml_app}"
        display_type = "line"
      }
    }
  }
  widget {
    timeseries_definition {
      title = "MCP tool calls (Datadog MCP)"
      request {
        q            = "sum:trace.mcp.request{*}.as_count()"
        display_type = "bars"
      }
    }
  }
  widget {
    timeseries_definition {
      title = "Bedrock calls by domain"
      request {
        q            = "sum:sre_agent.bedrock.calls{*} by {domain}.as_count()"
        display_type = "bars"
      }
    }
  }

  # ── Row 4: quality & safety (the differentiator) ─────────────────────
  widget {
    timeseries_definition {
      title = "Grounding score by domain (firewall judge)"
      request {
        q            = "avg:sre_agent.grounding_score{*} by {domain}"
        display_type = "line"
      }
      yaxis {
        min = "0"
        max = "1"
      }
    }
  }
  widget {
    timeseries_definition {
      title = "Answer quality SLI — grounded vs total"
      request {
        q            = "sum:sre_agent.answer.grounded{*}.as_count()"
        display_type = "bars"
      }
      request {
        q            = "sum:sre_agent.answer.total{*}.as_count()"
        display_type = "line"
      }
    }
  }
  widget {
    timeseries_definition {
      title = "Hallucination (withheld) vs guardrail blocks"
      request {
        q            = "sum:sre_agent.hallucination{*}.as_count()"
        display_type = "bars"
      }
      request {
        q            = "sum:sre_agent.guardrail_blocks{*}.as_count()"
        display_type = "bars"
      }
    }
  }
  widget {
    timeseries_definition {
      title = "LLM errors"
      request {
        q            = "sum:ml_obs.trace.error{$ml_app}.as_count()"
        display_type = "bars"
      }
    }
  }
}

output "dashboard_url" {
  value = "https://app.us5.datadoghq.com${datadog_dashboard.codecraft_sre.url}"
}
