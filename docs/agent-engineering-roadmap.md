# WeavePath Agent Engineering Roadmap

## Product intent

WeavePath is intended to become a **Visual Agent Engineering Workspace**: a place to design, run, inspect, compare, and hand off agent work across standalone models and external hosts such as Codex and Claude Code.

The long-term product is broader than a branching chat interface. Its durable unit is a route-aware execution record that connects conversation state, tool activity, artifacts, evaluations, experiments, and external agent runs without silently mixing sibling histories.

This document is a public roadmap, not a statement that every listed capability exists today. Each phase has explicit exit criteria so planned work is not presented as shipped functionality.

## What exists today

The current **Conversation Workflow / Local Graph Chat** implementation is the Phase 1 foundation. It already demonstrates:

- a local-first FastAPI, React, and SQLite vertical slice;
- workflows, topics, concrete conversation instances, immutable parent routes, checkpoints, local messages, and tombstones;
- branch, activate, inspect, topic-route selection, and two-step cascade archival;
- isolation between concrete routes, including multiple instances of the same logical topic;
- a standalone OpenAI-compatible synchronous chat path and in-app model settings;
- a native `WorkspaceShell` with “Chat / Workflow / Lab” switching, a top-level `ConversationInstance` graph, an actionable local-only Turn Canvas, and Chinese/English UI chrome; `/graph` remains a compatibility entry point;
- separation between inherited checkpoint memory and messages written locally to the selected node;
- revision-aware editing and regeneration of the latest local user message.
- an Engineering Lab preview for transcript-free branch comparison, explicit knowledge transfer, versioned artifacts and datasets, and frozen experiment snapshots.

Phase 1 is **not complete**. A schema v5 forward migration runner now exists, but rollback/downgrade policy, migration release tooling, streaming generation, cancellation, retry-without-edit, a stable host adapter layer, and production deployment controls remain future work. The current Codex bridge is a legacy adapter and reference implementation, not the long-term system of record.

A **Verified local preview** named **Route-to-Agent Run v1** now exercises one bounded synchronous run from a frozen concrete-route context, with an execution brief, durable event journal, `safe_calculator` / `1.0.0`, the configured OpenAI-compatible production adapter, a test-only scripted mock, revision/idempotency/interruption safeguards, and a readable web execution timeline. A separate **Engineering Lab v1** slice adds branch comparison, explicit accepted-knowledge records, versioned artifacts/datasets, and immutable experiment snapshots. These narrow previews do not make Phase 1, Phase 2, Phase 5, or Phase 7 complete.

## Guiding principles

1. **Routes are explicit.** Every concrete run has one immutable ancestry route. A shared topic does not imply shared mutable memory.
2. **Runs are reproducible.** Inputs, tools, model configuration, artifacts, and evaluation results must have stable identities and provenance.
3. **The graph is operational.** Nodes represent real conversations or agent runs, not decorative summaries.
4. **Hosts are adapters.** Codex, Claude Code, model providers, and local runtimes integrate through capability-aware ports; none owns the core graph.
5. **Human control is visible.** The system may recommend branches, retries, or transfers, but consequential graph changes and external actions remain inspectable and policy-controlled.
6. **Failure is first-class.** Cancellation, partial tool execution, retries, orphan recovery, and revision conflicts are modeled rather than hidden.
7. **Local-first is the baseline.** Cloud deployment and collaboration must preserve the same ownership, isolation, and audit guarantees.

## Target architecture

```text
Web Workspace / Desktop Companion / Codex Adapter / Claude Adapter / API Clients
                                  │
                       Application Command Layer
                                  │
       ┌──────────────────────────┼──────────────────────────┐
       │                          │                          │
 Agent Runtime              Workflow Graph             Route Memory
 runs · tools · events      nodes · routes · sagas     checkpoints · context
       │                          │                          │
       ├───────────────┬──────────┴──────────┬──────────────┤
       │               │                     │              │
  Artifacts       Experiments & Eval    Tracing & Search   Automation
       │               │                     │              │
       └───────────────┴──────────┬──────────┴──────────────┘
                                  │
                       Ports and Host Adapters
               Standalone · Codex · Claude · Model Providers
                                  │
                     SQLite first · deployable stores later
```

The graph core remains independent of provider SDKs, host APIs, widget code, and filesystem-specific behavior. Integrations report capabilities and execute commands through adapters.

## Phased roadmap

### Phase 0 — Product boundaries and invariants

**Status: complete.**

Established the local-first source of truth, route-instance model, checkpoint semantics, revision boundaries, cascade archival behavior, and host-adapter direction. Legacy manifest and Codex bridge behavior are documented for migration rather than treated as the future architecture.

**Exit evidence**

- accepted architecture decisions for the global store, topic route instances, host-operation sagas, and the native double-canvas workspace;
- documented data ownership and legacy manifest migration;
- graph invariants covered by domain tests.

### Phase 1 — Conversation Workflow foundation

**Status: in progress.**

Deliver a reliable standalone route-aware conversation graph before expanding into a general agent runtime.

**Current capabilities** are listed in “What exists today.” Remaining work includes:

- hardening the existing schema v5 forward migrations with upgrade matrices, backup/recovery, and rollback/downgrade policy;
- extending restart, archive, and conflict E2E coverage; `/graph` popup lifecycle is compatibility coverage, not a completion condition for the default WorkspaceShell;
- stable command/query application services and adapter contracts;
- clearer recovery for partially completed operations;
- packaging and data-directory diagnostics suitable for non-developer users.

**Exit criteria**

- route isolation and frozen checkpoints survive restart and migration;
- graph mutations are revision-safe and idempotent where required;
- the standalone app completes create, branch, switch, inspect, and archive workflows without Codex or Claude;
- legacy manifests can be imported without making them the active source of truth;
- failures produce stable, documented error codes.

### Phase 2 — Agent runtime and tool registry

Introduce a host-neutral runtime for executing an agent turn as a durable run rather than a single opaque HTTP request.

**Verified local preview:** Route-to-Agent Run v1 exercises a synchronous subset of this phase with one registered tool (`safe_calculator` / `1.0.0`), the configured OpenAI-compatible production adapter, and a test-only `ScriptedMockAgentAdapter`. The recorded local test and browser evidence validates this first durable run boundary; it does not complete Phase 2 or imply streaming, cancellation, arbitrary shell/file/network tools, approvals, artifacts, evaluation, multi-agent behavior, or formal Codex/Claude adapters.

**Scope**

- `AgentRun`, `RunStep`, `ToolCall`, `ToolResult`, and lifecycle event models;
- capability-based tool registry with schemas, versions, provenance, and policy metadata;
- provider-neutral message and tool-call ports;
- run states such as queued, running, awaiting approval, cancelling, cancelled, failed, and completed;
- durable event journal and resumable command handling;
- deterministic mock runtime for contract and failure testing.

**Exit criteria**

- one route can execute a multi-step tool-using run with a complete event history;
- unknown or version-incompatible tools fail before execution;
- a process restart can recover or clearly terminate an interrupted run;
- graph state changes occur through explicit runtime events, not provider-specific callbacks.

### Phase 3 — Route-aware memory and context engineering

Expand checkpoint inheritance into an inspectable context system.

**Scope**

- foundation, route digest, current brief, and context-budget layers;
- deterministic context assembly from one concrete route;
- provenance on every included summary, message, artifact excerpt, or transferred fact;
- explicit cross-route transfer with user approval and an immutable transfer record;
- background summarization with instance and content-revision guards;
- context previews showing what the next run will receive.

**Exit criteria**

- sibling-only facts never enter a run without an explicit transfer;
- stale background summaries cannot overwrite a newer instance revision;
- users can inspect and export the exact context envelope for a run;
- context-budget behavior is testable with golden fixtures.

### Phase 4 — Streaming, cancellation, and retry semantics

Make long-running agent behavior interactive and recoverable.

**Scope**

- SSE or an equivalent event stream for model output and runtime events;
- cooperative cancellation propagated from UI to provider and tools;
- retry of the last response without editing the user message;
- retry policies for transient model and tool failures;
- idempotency keys and attempt lineage;
- clear distinction between regenerate, resume, replay, and fork.

**Exit criteria**

- cancelling a run stops further model output and prevents uncommitted assistant content from appearing as complete;
- every retry is a new attempt linked to its original run;
- tool side effects are never blindly repeated after an ambiguous failure;
- reconnecting clients can resume the event stream without duplicated UI output.

### Phase 5 — Evaluation and experiments

Turn routes into comparable engineering experiments rather than informal chat branches.

**Scope**

- versioned datasets, cases, evaluators, scorers, and experiment definitions;
- parameter matrices across prompts, models, tools, memory policies, and routes;
- baseline and candidate comparisons with reproducible run manifests;
- deterministic, model-based, and human-review evaluation modes;
- regression gates and result dashboards;
- cost, latency, tool reliability, and task-quality metrics.

**Exit criteria**

- an experiment can be reproduced from a pinned definition and input snapshot;
- results distinguish model output, evaluator output, and human judgment;
- comparisons preserve route and configuration provenance;
- CI or automation can consume a stable machine-readable evaluation result.

### Phase 6 — Observability, tracing, and search

Provide engineering-grade visibility into how an answer or artifact was produced.

**Scope**

- trace and span model covering model calls, tool calls, memory assembly, handoffs, and artifact writes;
- structured logs, latency, token/cost accounting, and error classification;
- route timeline and run replay views;
- search over graph metadata and explicitly indexed content;
- export to standard observability formats where practical;
- privacy-aware retention and redaction controls.

**Exit criteria**

- every run can be traced from user input to final outputs and external actions;
- search results identify route, instance, run, and provenance;
- sensitive fields are excluded or redacted by policy;
- telemetry can be disabled without breaking core execution.

### Phase 7 — Artifacts as first-class outputs

Connect files and structured results to the route and run that produced them.

**Scope**

- artifact identities, versions, MIME/type metadata, checksums, lineage, and ownership;
- previews and diffs for code, documents, datasets, reports, and experiment outputs;
- explicit relationships among source inputs, generated artifacts, evaluations, and routes;
- external filesystem references without silently copying private data;
- export bundles with provenance manifests.

**Exit criteria**

- an artifact can be traced to its creating run, tools, inputs, and route;
- overwrites create a new version or an explicit replacement event;
- missing or externally moved files degrade visibly;
- export does not include sibling-route or private content implicitly.

### Phase 8 — Multi-agent orchestration and handoff

Support collaboration among specialized agents without collapsing their memories.

**Scope**

- agent roles, capabilities, assignments, dependencies, and run ownership;
- parallel sub-runs with bounded inputs and independent route context;
- structured handoff briefs containing goals, constraints, artifacts, and provenance;
- accept, reject, revise, and return-for-clarification handoff states;
- merge proposals that require explicit review rather than transcript concatenation;
- deadlock, timeout, budget, and cancellation policies.

**Exit criteria**

- parallel agents cannot read sibling context unless the orchestration plan grants it;
- every handoff records exactly what was transferred;
- parent runs can wait, cancel, or continue after partial child completion;
- multi-agent failures remain inspectable and do not corrupt the workflow graph.

### Phase 9 — Codex and Claude adapters

Connect external coding agents through capability-aware adapters while keeping WeavePath authoritative for its own graph metadata.

**Codex scope**

- import legacy manifest v1 data;
- bind and navigate real Codex tasks;
- fork, inspect, rename, archive, and recover orphaned operations through supported host capabilities;
- retain a lightweight widget or plugin entry point while the full workspace remains independent.

**Claude scope**

- map available Claude Code sessions, commands, hooks, and artifacts to the same adapter contract;
- expose unsupported native actions as explicit companion-app fallbacks;
- avoid assuming Codex and Claude share identical transcript or lifecycle semantics.

**Exit criteria**

- adapter contract suites pass against mocks and supported real hosts;
- capability discovery prevents unsupported operations from being presented as successful;
- host failure during fork/archive/handoff has a documented recovery path;
- no adapter bypasses route isolation, revision checks, or the core audit log.

### Phase 10 — Automation and reusable engineering workflows

Make validated agent processes repeatable.

**Scope**

- workflow templates with typed inputs and versioned definitions;
- scheduled and event-triggered runs;
- monitors, budgets, approval gates, and escalation policies;
- evaluation gates before downstream execution or artifact publication;
- run queues, concurrency limits, and notification policies.

**Exit criteria**

- automated runs use the same runtime, trace, security, and route rules as interactive runs;
- schedules and triggers are auditable and individually pausable;
- high-impact actions require configured approval gates;
- template upgrades do not silently alter already running workflows.

### Phase 11 — Security, packaging, and deployment

Prepare WeavePath for dependable local use and optional managed deployment.

**Scope**

- desktop packaging, single-instance behavior, local service discovery, and loopback authentication;
- credential vault integration and strict secret redaction;
- tool permissions, filesystem/network scopes, sandbox profiles, and approval policies;
- signed plugins/adapters and dependency provenance;
- encrypted backups, migration tooling, retention, export, and deletion controls;
- deployable database/object-store ports, tenant isolation, and audit administration;
- threat modeling and security regression tests.

**Exit criteria**

- local installs have predictable data ownership, backup, upgrade, and recovery behavior;
- credentials never enter workflow manifests, traces, or client-readable settings;
- adapter and tool permissions are visible before execution;
- any hosted mode demonstrates tenant isolation and auditable administration before collaboration is enabled.

## Cross-phase engineering tracks

These are continuous requirements, not a final polish phase:

- **Schema evolution:** explicit migrations, compatibility windows, fixtures, and rollback plans.
- **API discipline:** versioned contracts, stable errors, idempotency, pagination, and capability discovery.
- **Testing:** domain invariants, adapter contracts, deterministic runtime tests, browser E2E, migration tests, and failure injection.
- **Accessibility and internationalization:** keyboard-complete graph navigation, reduced motion, high-contrast states, screen-reader semantics, and UI-only translation that never rewrites user data.
- **Performance:** bounded context assembly, virtualized histories, incremental graph loading, backpressure, and measurable latency budgets.
- **Documentation:** public capability matrix, data model, security model, adapter guide, and reproducible examples.

## Public capability language

Public releases should use the following labels:

- **Available:** implemented, documented, and covered by automated or recorded acceptance evidence.
- **Preview:** usable end to end but with documented limits or migration risk.
- **Verified local preview:** exercised end to end on the recorded local environment, with its unsupported production and platform boundaries stated explicitly.
- **Planned:** designed or scheduled but not available to users.
- **Exploratory:** under evaluation with no compatibility commitment.

Roadmap placement alone never changes a feature to Available. Each release should link claims to tests, supported platforms, and known limitations.

## Near-term sequence

The recommended execution order is:

1. finish Phase 1 persistence, recovery, and adapter boundaries;
2. introduce the durable Agent Runtime and Tool Registry;
3. complete route-aware context envelopes;
4. add streaming, cancellation, and explicit retry attempts;
5. build evaluation and tracing on the same run/event model;
6. promote artifacts and handoffs to first-class graph relationships;
7. validate Codex and Claude adapters against the stable core;
8. add automation only after runtime, evaluation, and security controls are dependable;
9. package and deploy without weakening local-first ownership or route isolation.

This sequence keeps WeavePath useful at every phase while steadily evolving Conversation Workflow into a representative-quality Visual Agent Engineering Workspace.
