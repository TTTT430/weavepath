# Route-to-Agent Run v1

> **Status: Verified local preview.** On 2026-09-01 this narrow slice passed the recorded backend, frontend, migration, restart, build, and desktop-browser checks below. This is not a production-ready Agent Runtime and does not complete Phase 1 or Phase 2.

## Purpose and boundary

Route-to-Agent Run v1 is the first narrow bridge from one concrete Conversation Workflow route to a durable Agent Run. It is synchronous and bounded; it is not a general Agent Runtime or a multi-agent orchestrator.

The verified local preview currently contains:

- an execution brief with `objective`, `constraints`, `deliverables`, and `acceptanceChecks`;
- a frozen context snapshot for one active conversation instance and its accepted content revision;
- persisted run, step, event, tool-call, tool-result, and immutable final-answer records;
- one registered tool: `safe_calculator` / `1.0.0`;
- an OpenAI-compatible production adapter using the provider configured in WeavePath;
- a deterministic `ScriptedMockAgentAdapter` available only through test injection;
- idempotent creation, revision checks, terminal failure records, and startup interruption recovery;
- a web execution-brief dialog and persisted run/timeline panel.

The POST request remains open until the bounded model/tool loop reaches a terminal state. A concurrent replay with the same idempotency key is serialized behind the original request and receives that terminal run rather than a stale `queued`/`running` snapshot. “Durable” means the accepted input, frozen context, state, event journal, tool records, and final answer survive a process restart. It does not mean background execution.

## Explicit non-goals

The v1 preview does **not** provide:

- SSE, token streaming, or a background run queue;
- cancellation, pause, resume, replay, or retry-attempt lineage;
- arbitrary shell, filesystem, network, plugin, MCP, or marketplace tools;
- artifacts or artifact previews;
- evaluation datasets, experiments, scorers, or regression gates;
- multi-agent orchestration or handoff;
- a formal Codex or Claude adapter;
- multiple API worker processes, distributed leases, or cross-process run ownership;
- authentication, tenant authorization, cloud collaboration, or production public deployment.

The UI may close an unsubmitted brief. It must not label that action as cancellation of a started run.

## Current execution flow

1. The single-process local server validates the request body and serializes the idempotency key within the selected workflow and instance before binding a provider snapshot.
2. It reads the selected instance's effective message snapshot and rejects a stale `expectedContentRevision` before creating a run.
3. It binds one snapshot of the configured model adapter and builds a context envelope containing the concrete route, available tools, effective messages, and brief.
4. It creates a `queued` run plus `run.created` and `context.frozen` events, then moves the run to `running`.
5. A bounded model/tool loop records model and tool activity. Only the registered calculator can execute.
6. A final answer is committed only if the target instance is still active and its content revision still matches the run input revision. The commit appends one local assistant message, increments content revisions, and stores a separate immutable `final_answer` on the run.
7. A failure after run creation leaves a terminal failed run and journal. The API error should carry its `runId` so the client can inspect it.
8. On service startup, persisted `queued` or `running` rows become `interrupted` with `runInterrupted`; the implementation does not resume them. Because this recovery is process-wide, v1 must run with one API worker.

Run states in this slice are `queued`, `running`, `completed`, `failed`, and `interrupted`.

## Frozen concrete-route context

The persisted `context_snapshot_json` currently contains:

```json
{
  "workflowId": "wf_…",
  "instanceId": "ci_…",
  "inputContentRevision": 3,
  "memoryRoute": [
    {"instanceId": "ci_root", "topicId": "topic_a", "title": "A"},
    {"instanceId": "ci_selected", "topicId": "topic_b", "title": "B"}
  ],
  "availableTools": [
    {"name": "safe_calculator", "version": "1.0.0", "sideEffect": "none"}
  ],
  "messages": [],
  "objective": "Calculate the requested value",
  "constraints": ["Use the registered calculator"],
  "deliverables": ["A numeric result"],
  "acceptanceChecks": ["Arithmetic is correct"]
}
```

The actual tool snapshot also contains its description and JSON schema. The context is hashed with SHA-256 and is not recomputed when a completed run is read.

Required invariants:

1. `instanceId` belongs to `workflowId` and is active.
2. `memoryRoute` is one parent chain ending at that instance.
3. Effective messages contain the current messages resolved recursively along the instance's parent route plus its local messages, never sibling-route local messages. The immutable checkpoint snapshot remains an audit record and is not the live inheritance source.
4. The accepted content revision is stored on the run.
5. A stale revision creates no run, step, event, or tool record.
6. Later chat regeneration may replace the visible assistant message but cannot change the run's stored final answer.

The public run summary/detail exposes `contextSha256`, `memoryRoute`, and `availableTools`, but not the frozen messages or full context snapshot. Direct context export and full provenance inspection are later API work.

## Model snapshot and credentials

The production path uses the OpenAI-compatible provider already configured for WeavePath. The create request does not let a client select an arbitrary adapter or model.

Only allowlisted non-credential model metadata is persisted: provider, model, base URL, timeout, system prompt, and optional adapter version. API keys, bearer tokens, request headers, environment variables, and arbitrary custom-adapter fields are excluded. Provider failures returned to clients and journals must remain redacted.

`ScriptedMockAgentAdapter` is a deterministic test fixture. It is not a model-settings option and must not be presented as a production provider.

## Registered tool

The only registered tool identity is:

```text
name:    safe_calculator
version: 1.0.0
effect:  none
input:   {"expression": "string, 1-200 characters"}
```

The implementation parses a restricted arithmetic expression AST. It supports numeric constants, unary `+/-`, arithmetic operators, and bounded exponentiation. It rejects unsupported syntax, non-finite values, excessive depth, large exponents, and out-of-range results. It has no shell, filesystem, network, environment, or dynamic-import capability.

Tool name, version, arguments, result or stable error code, duration, and output hash are persisted. A future behavior or schema change requires a new version or an explicitly documented compatible revision.

## HTTP API

These are the current paths:

```text
POST /api/v1/workflows/{workflowId}/instances/{instanceId}/runs
GET  /api/v1/workflows/{workflowId}/instances/{instanceId}/runs
GET  /api/v1/runs/{runId}
GET  /api/v1/runs/{runId}/events?afterSequence=0&limit=100
```

There are no nested `.../instances/{instanceId}/runs/{runId}` detail or event routes.

### Create request

```json
{
  "objective": "Use the calculator to evaluate (18 + 24) / 6.",
  "constraints": ["Use only the registered calculator"],
  "deliverables": ["The result and a short explanation"],
  "acceptanceChecks": ["The result equals 7"],
  "expectedContentRevision": 3,
  "idempotencyKey": "client-generated-key"
}
```

The server selects the configured OpenAI-compatible adapter and the server-side tool registry. `adapter`, `model`, and `tool` are deliberately not accepted request fields.

### Run summary and detail

A run summary contains:

```json
{
  "runId": "run_…",
  "workflowId": "wf_…",
  "instanceId": "ci_…",
  "status": "completed",
  "inputContentRevision": 3,
  "contextSha256": "…",
  "modelSnapshot": {"provider": "openai-compatible", "model": "…"},
  "memoryRoute": [
    {"instanceId": "ci_root", "topicId": "topic_a", "title": "A"},
    {"instanceId": "ci_selected", "topicId": "topic_b", "title": "B"}
  ],
  "availableTools": [
    {"name": "safe_calculator", "version": "1.0.0", "sideEffect": "none"}
  ],
  "objective": "…",
  "constraints": [],
  "deliverables": [],
  "acceptanceChecks": [],
  "finalMessageId": 123,
  "errorCode": null,
  "createdAt": "…",
  "updatedAt": "…"
}
```

The create response for a completed run and `GET /api/v1/runs/{runId}` also include `finalAnswer`. The detail endpoint additionally includes `steps`, `toolCalls`, and `toolResults`. The instance-scoped list returns `{"runs": [...]}` with summaries.

The event endpoint returns:

```json
{
  "runId": "run_…",
  "events": [
    {"sequence": 1, "type": "run.created", "payload": {}, "createdAt": "…"}
  ],
  "nextAfterSequence": 1
}
```

`afterSequence` is exclusive; `limit` accepts 1-500. Clients must continue paging when they need more than one page.

### Global run ID boundary

Run detail and event reads use a global `runId`, without workflow or instance path parameters. This is acceptable only for the current single-user local preview. The API has no authentication or tenant authorization and must not be exposed directly to the internet. A hosted or multi-user version must add an authorization boundary rather than treating an opaque run ID as access control.

### Error semantics

| Condition | Current stable result |
|---|---|
| invalid/blank/oversized brief | `422 validationError`; no run created |
| unknown workflow or instance | `404 notFound`; no run created |
| inactive/pruned target | `409 runTargetInactive`; no run created |
| stale revision before creation | `409 runRevisionConflict`; no run created |
| same key and identical request while original is active | wait for and return the original terminal run; do not bind or execute again |
| same key and identical request after terminal failure | `201` with the original terminal failed run; no retry attempt is created |
| same key and different request | `409 idempotencyConflict` |
| provider not configured or unavailable | `503 aiUnavailable`; includes `runId` only if a run was created |
| provider timeout | `504 aiTimeout` and terminal failed run with `runId` |
| invalid provider protocol | `502 modelProtocolError` and terminal failed run with `runId` |
| unknown tool requested by model | `422 unknownTool` and terminal failed run with `runId` |
| invalid tool arguments | `422 toolArgumentsInvalid` and terminal failed run with `runId` |
| calculator execution failure | `500 toolExecutionFailed` and terminal failed run with `runId` |
| target revision changes during execution | `409 runRevisionConflict`; no final assistant write |

Failure bodies, stable error codes, zero-write preflight failures, and post-creation `runId` recovery are covered by the recorded backend and frontend suites. The failed-run UI path is component-tested; the recorded real-browser E2E exercised the successful path.

## Event journal

Current event types include:

```text
run.created
context.frozen
run.started
model.started
model.completed | model.failed
tool.requested
tool.started
tool.completed | tool.failed
run.completed | run.failed | run.interrupted
```

Events use a stable run ID and a monotonically increasing per-run sequence. Payloads contain bounded metadata such as revision, step sequence, tool identity, error code, hashes, and final message ID; credentials and raw provider errors must not be written.

## UI verification record

The current preview provides a selected-node “Agent” action, a brief dialog, route and provenance fields, a confirmation checkbox, an instance-scoped run list, and a detail/timeline dialog. It performs one synchronous create request and reloads persisted detail/events.

The 47-test frontend suite records route-owned request locks, same-brief idempotency-key reuse, failed-run recovery through `ApiError.runId`, revision-aware completion refresh, event pagination, an explicit synchronous wait state, focus return/Tab trapping/Escape handling, and the narrow-screen single-column CSS behavior. It verifies that:

- workflow ID, instance ID, accepted revision, provider/model, and `safe_calculator` / `1.0.0` remain bound to the selected route;
- pending list/detail/create responses cannot leak across route switches;
- an uncertain retry of the same brief reuses its key, while an edited brief gets a new key;
- a POST failure with `runId` opens the persisted failed timeline and preserves its stable code;
- completion refresh applies only to the owning route and cannot overwrite a newer chat revision;
- event pagination has no duplicate or skipped sequence;
- closing the surface during a synchronous wait never claims to cancel server execution;
- focus return and Escape handling remain test-covered, and the narrow-screen layout stays in the reviewed CSS path.

The desktop-browser success-path run additionally verified visible workflow/instance/revision/model/tool provenance, the concrete memory route, persisted run count and timeline, route switching without sibling-message leakage, and restoration of the completed run after switching away and back. The browser capability did not establish a genuine narrow viewport, so responsive behavior remains supported by code and component tests rather than a recorded narrow-screen browser claim. The persisted failed-run surface is also covered by component tests, not by this success-path browser run.

## OpenAI-compatible adapter verification record

The preview adapter sends `parallel_tool_calls: false`, accepts only one function tool call, validates JSON object arguments, rejects unsupported `finish_reason` values, and rejects truncated or filtered output. Automated fake-upstream tests cover:

- `finish_reason` and truncated-response handling;
- parallel/multiple tool-call rejection;
- malformed tool-call arguments and multiple tool-call responses;
- redaction of provider errors and credentials;
- one bound model configuration for the lifetime of a run.

The real-browser E2E used this production OpenAI-compatible adapter against a controlled local upstream rather than `ScriptedMockAgentAdapter`. It completed a calculator tool round trip while the upstream was configured to fail if a sibling-route canary appeared.

## Acceptance matrix

Evidence recorded on 2026-09-01:

| Area | Acceptance case | Status | Recorded evidence |
|---|---|---|---|
| Brief | workflow, instance, route, model, tool version, and revision are visible | Verified: automated + browser | component suite and desktop-browser success path |
| Route isolation | a sibling-only canary never enters context or provider messages | Verified: automated + browser | repository/API assertions and controlled-upstream E2E |
| Frozen context | stored route/messages/tools/brief and hash remain stable after later writes | Verified: automated | repository/API assertion after a later route write |
| Journal | ordered events, terminal state, and redacted payload survive restart | Verified: automated | database reopen plus detail/events API assertion |
| Final answer | completed result survives chat regeneration and v2→v3 migration | Verified: automated | regeneration, migration, and database-reopen tests |
| Calculator safety | NaN/Infinity, deep/large exponent, unsupported syntax, and oversized input fail safely | Verified: automated | tool registry/security tests, including the 200-character boundary |
| Scripted mock | deterministic success/failure fixtures create stable journals | Verified: automated | test-adapter contract tests |
| OpenAI-compatible | only frozen route context is sent; protocol edge cases fail closed | Verified: automated + browser | fake-upstream contract tests and controlled-upstream E2E |
| Idempotency | identical retry returns one run; changed payload conflicts | Verified: automated | sequential and concurrent API/DB assertions |
| Revision | stale creation has zero side effects; late completion cannot revive a terminal run | Verified: automated | state-machine/API tests |
| Recovery | queued/running become interrupted once; completed results reload | Verified: automated | startup and database-reopen tests |
| Single-instance startup | official factory locks before migration/recovery and releases for restart | Verified: automated | second-process and startup-failure tests on Windows |
| Failed-run UI | POST error `runId` opens the persisted failed timeline | Verified: automated | frontend component/API tests; browser failure path not yet recorded |
| Route-switch UI | Run/chat snapshots cannot cross routes or replace newer revisions | Verified: automated + browser | frontend race tests and desktop-browser route switching |
| Build | backend tests, compileall, frontend tests, typecheck, and production build pass together | Verified | 86 backend tests, 47 frontend tests, compileall/typecheck/build |
| Browser E2E | a controlled OpenAI-compatible test upstream → tool → timeline → final message works on isolated routes | Verified: desktop success path | 11-event run returned `E2E route A-B result: 42`; sibling canary absent |

## Supported claim and remaining limits

The supported narrow claim is:

> “WeavePath can start one bounded synchronous run from a frozen concrete-route context, persist its execution brief and event timeline, and execute `safe_calculator` / `1.0.0` through the configured OpenAI-compatible provider.”

`ScriptedMockAgentAdapter` remains test-only, and streaming, cancellation, arbitrary shell/file/network tools, artifacts, evaluation, multi-agent handoff, and formal Codex/Claude adapters remain outside v1.

The preview is restricted to one local API process and one Uvicorn worker. The official `uvicorn api.app:create_app --factory` path acquires an OS sidecar lock before migration and startup recovery. Every launch must use the same canonical `WEAVEPATH_DB` spelling; hard links and mapped-drive/UNC aliases can produce different lock paths and are outside the guarantee. Store injection and one-shot helpers are testing/embedding paths and do not replace the official server ownership boundary.
