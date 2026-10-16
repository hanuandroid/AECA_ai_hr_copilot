# AI provider setup: Anthropic or OpenAI

**Status (2026-10-16):** both providers are implemented behind the `LLMProvider` abstraction and **verified only with mocked clients**. **No live call has been made with either provider.** Do not send real employee data to either provider until the data-processing decisions are approved (`production-hardening/data-processing.md` §5, including the OpenAI items 7–11).

## 1. Choosing a provider

There is exactly one active LLM provider per deployment, chosen by configuration only:

| `AI_LLM_PROVIDER` | Needs | Models |
|---|---|---|
| `anthropic` (default) | `ANTHROPIC_API_KEY` | `AI_LLM_MODEL`, `AI_LLM_FAST_MODEL` |
| `openai` | `OPENAI_API_KEY` **and** `AI_OPENAI_MODEL` | `AI_OPENAI_MODEL`, and `AI_OPENAI_FAST_MODEL` (optional; falls back to `AI_OPENAI_MODEL`) |
| `fake` | nothing | Tests only; refused in production |

How failures behave:
- A missing key, a missing model, an unknown model (404) or an authentication failure gives `AI_UNAVAILABLE` (503).
- Timeouts, rate limits and server errors give `AI_PROVIDER_ERROR` (502).
- Invalid or truncated output gives `AI_INVALID_OUTPUT` (502).
- There is **no automatic fallback** to the other provider, so data never goes to a processor you didn't select.
- In production, startup refuses a selected provider that has no key or model.

Keys are read only from the server environment (`SecretStr`). They are never sent to Next.js or the browser, never returned by the API, and never logged.

Embeddings are always local (fastembed) and need no key with either provider.

## 2. Configure OpenAI for local development

In `HRMS-main/backend/.env` (git-ignored; never commit it):

```bash
AI_ENABLED=true
AI_LLM_PROVIDER=openai
OPENAI_API_KEY=<from the OpenAI dashboard, your project's API keys>
AI_OPENAI_MODEL=<a model id available to your project that supports Structured Outputs (JSON schema)>
# optional:
# AI_OPENAI_FAST_MODEL=<smaller model for routing classification>
# AI_OPENAI_REASONING_EFFORT=low        # only for reasoning-capable models
# AI_OPENAI_INPUT_COST_PER_MTOK=...     # your contract's USD per million tokens, for ESTIMATED cost
# AI_OPENAI_OUTPUT_COST_PER_MTOK=...
```

Restart the API with `docker compose restart api`.

No model id is built in. Choose one from your OpenAI project and confirm it supports Structured Outputs. Leave `AI_TEMPERATURE` unset unless the model accepts it.

Switching back is configuration only: set `AI_LLM_PROVIDER=anthropic` with `ANTHROPIC_API_KEY`. The Anthropic implementation is unchanged.

## 3. How the OpenAI provider works (`backend/app/services/ai/llm/openai_provider.py`)

| Topic | Behaviour |
|---|---|
| API | Responses API (`responses.create`), SDK `openai==3.23.0`, which shares `httpx2` with `anthropic==1.9.0` |
| Structured output | Every call site requests a Pydantic schema: router classification, policy answer, SQL generation, SQL wording and action proposal. The provider sends a **strict JSON-schema** text format derived from that model: all objects closed, all fields required, optionals nullable. Value constraints (lengths, ranges) are not sent but are **enforced locally**, because the returned JSON is validated against the Pydantic model like any untrusted model output. No tool or function calling is used; the app doesn't need it. |
| Data handling | `store=false` on every request. The base URL is pinned to `https://api.openai.com/v1`, so `OPENAI_BASE_URL` is ignored. The SDK may still read `OPENAI_ORG_ID` / `OPENAI_PROJECT_ID` from the environment; these are routing headers only. |
| Errors | Refusal → `AI_PROVIDER_ERROR`. Status `incomplete`: `max_output_tokens` → `AI_INVALID_OUTPUT`, `content_filter` → `AI_PROVIDER_ERROR`. 401/403 → unavailable. 404 (model) → unavailable. 429, 400, 5xx and connection errors → provider error. Timeout → provider error. The SDK retries twice; there are no application-level retries of mutations. |
| Usage and cost | `input_tokens` and `output_tokens` from the response go into the existing audit, daily token budget and usage dashboard. Missing usage is recorded as **unknown (null), never 0**. Estimated cost uses only the `AI_OPENAI_*_COST_PER_MTOK` rates; Anthropic rates are never applied to OpenAI models. Concurrency slots and rate limits are unchanged. |
| Logging | Provider, model, status, request id and error types only. Never prompts, completions or keys. |

## 4. Run the mocked tests (no key, no network)

```bash
cd HRMS-main/backend
python -m pytest -q tests/ai/test_openai_provider.py tests/ai/hardening/test_openai_pipeline.py
python -m pytest -q                       # full suite: the default test provider is still "fake"
```

What they cover:
- strict schemas for every app schema;
- request shape;
- response translation and refusal / incomplete handling;
- every SDK error class, with no key or prompt leakage;
- provider selection, with no cross-provider fallback;
- production checks;
- usage and cost;
- the full `POST /chat` pipeline on OpenAI: policy citations, hostile SQL blocked, actions still needing confirmation, invalid output failing closed.

## 5. Approved smoke test (only after a key and live testing are approved)

Use synthetic content only and a dev database seeded with demo data (`scripts/seed.py`).

1. **Minimal call:**

   ```bash
   docker compose exec api python -c "import asyncio; from app.services.ai.providers import build_llm_provider; from app.services.ai.llm.base import LLMMessage; print(asyncio.run(build_llm_provider().generate([LLMMessage(role='user', content='Reply with the word ok.')], max_tokens=256)).model)"
   ```

   This checks the selected model id is returned. Check the API logs show `ai.llm.completed` without the prompt text.
2. **Policy RAG:** log in as the demo employee and ask "How many sick leave days do I get?". The citations must be `Leave Policy` excerpts that were actually retrieved.
3. **SQL safety:** ask for salaries or bank details. It must be refused or blocked, and no data may change. The SQL agent stays read-only regardless of the model.
4. **Action safety:** ask the Copilot to deactivate an employee. The response must be `confirmation_required`, and nothing changes until the confirm endpoint is called.
5. **Usage:** open `/admin/ai-usage`. Provider `openai`, model, tokens, and estimated cost (only if prices are configured) appear without prompt content.
6. Record results and token usage in `ai_eval_results.md`. Live evaluations stay **NOT RUN** until performed.

## 6. Known limitations and blockers

- **No live verification yet:** schema acceptance by a specific model, output quality, latency and cost are unverified.
- **Strict mode:** a model without Structured Outputs support will fail with `AI_PROVIDER_ERROR` (400) or `AI_UNAVAILABLE` (404). That's safe, but the Copilot won't answer.
- **Anthropic-only options:** `AI_LLM_EFFORT` and `AI_LLM_SERVER_FALLBACKS` are ignored when `AI_LLM_PROVIDER=openai`. Use `AI_OPENAI_REASONING_EFFORT` instead.
- **Thresholds:** retrieval thresholds and the offline evals are provider-independent, but answer-quality acceptance needs a live evaluation per provider.
- **Governance blockers:** data categories, retention / ZDR / training settings, DPA and residency, PII minimisation, and employee notice. See `data-processing.md` §5.
