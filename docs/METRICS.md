# Metrics

## Product
- Daily/weekly active ministry use
- Number of care cases opened/resolved
- Number of visitor follow-up sequences prepared/sent
- Number of outreach drafts generated/used
- Briefing usefulness / operator confidence signal

## Growth
- Pilot demos run
- Pilot conversions
- Landing page conversion rate
- Qualified church leads in pipeline

## Business
- Active pilot accounts
- Pilot revenue
- Time-to-first-value for a new church pilot
- Conversion from pilot to recurring engagement

## Reliability / quality
- Sync success rate
- Briefing generation success rate
- Error rate on key pastoral workflows
- Backup/restore confidence and recovery readiness

## API observability instrumentation (launch baseline)

### Structured logs
- Every API request emits a structured log with:
  - `request_id`
  - `tenant_id` / `church_id` (from `X-Tenant-ID` / `X-Church-ID` headers when provided)
  - `route`, `method`, `status_code`
  - `latency_ms`
  - `error_class` for unhandled failures
- Sensitive content is intentionally excluded from logs:
  - no request/response bodies
  - no message/prayer/care note text
  - no member PII fields

### Exposed workflow metrics (`GET /metrics/workflows`)
- `briefing_generation_total{status=success|failure}`
- `chat_action_total{status=success|no_action|failure}`
- `care_prayer_crud_failures_total{route,method,error_class}`
- `rock_sync_outcome_total{status=success|degraded|disabled}`
- `mcp_tool_failures_total{tool,error_class}`
- Latency aggregates:
  - `http_request_latency_ms`
  - `briefing_generation_latency_ms`
  - `rock_sync_latency_ms`
  - `chat_fallback_latency_ms`

## Launch SLOs and alert thresholds

### 1) Availability SLO
- **SLO:** 99.5% successful API availability over rolling 30 days.
- **SLI:** non-5xx responses / total responses for core routes (`/briefing`, `/chat`, `/care`, `/members`, `/visitors`).
- **Alerts:**
  - warn if 5-minute availability < 99.0%
  - critical if 5-minute availability < 97.0%

### 2) API latency SLO
- **SLO:** p95 latency < 1200ms for core API routes over rolling 7 days.
- **SLI:** p95 of request latency by route family.
- **Alerts:**
  - warn if p95 > 1500ms for 10 minutes
  - critical if p95 > 2500ms for 10 minutes

### 3) Briefing generation success SLO
- **SLO:** 99.0% successful briefing generation (`/briefing/today`) daily.
- **SLI:** `briefing_generation_total{status=success}` / total `briefing_generation_total`.
- **Alerts:**
  - warn if daily success < 98.0%
  - critical if daily success < 95.0%

### 4) Rock sync success SLO
- **SLO:** 98.0% sync runs end in `success` over rolling 14 days.
- **SLI:** `rock_sync_outcome_total{status=success}` / total enabled sync runs.
- **Alerts:**
  - warn if 3 consecutive syncs are `degraded`
  - critical if 5 consecutive syncs are `degraded` OR any enabled run fails completely
