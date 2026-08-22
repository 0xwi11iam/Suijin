# ADR-001: LangGraph State Machine over Raw Asyncio Loop

- **Status**: Accepted
- **Date**: 2026-07
- **Deciders**: William Jiang

## Context

Suijin Red Team needs to orchestrate a multi-step attack pipeline: initialize context -> think (LLM reasoning) -> execute tool -> generate response -> loop. Each step may take seconds to minutes (LLM calls, network scans, exploitation attempts). The pipeline must:

1. **Persist state** across iterations (findings, flags, audit trail, tool outputs, cost tracking)
2. **Handle interruptions** gracefully (Ctrl+C pause, user guidance injection)
3. **Support parallel subagents** with crash isolation
4. **Enable supervisor oversight** every N iterations without coupling to the main loop
5. **Be debuggable** — state introspection at any node

## Options Considered

### Option A: Raw `asyncio` Event Loop

- Custom state machine built on `asyncio` with manual task management
- State stored in a shared dict with manual serialization
- Interrupts handled via signal handlers and custom event hooks
- Subagents via `asyncio.gather()` or `TaskGroup`

**Pros**: Zero dependencies, full control, minimal overhead
**Cons**: Reinventing state machine primitives (snapshots, replays, checkpoints), manual error boundaries, no built-in debugging tools, significant boilerplate for parallel subagent lifecycle management

### Option B: LangGraph State Machine

- Declarative graph definition: `initialize -> think -> execute_tool -> generate_response`
- Typed state (`AgentState`) with Pydantic validation
- Built-in checkpointing, state snapshots, and replay
- Native subgraph support for parallel execution
- `interrupt()` primitive for pause/resume cycles

**Pros**: Battle-tested state machine (used by LangChain ecosystem), typed state, built-in checkpointing, subgraph isolation, interrupt/resume primitives, minimal boilerplate
**Cons**: Additional dependency (~2MB), learning curve for graph concepts, opinionated structure

## Decision

**Chose Option B — LangGraph State Machine.**

The deciding factors:
1. **Checkpointing is free** — LangGraph snapshots state at every node transition without any manual serialization code
2. **Subgraph isolation** — one subagent crash doesn't kill the parent graph; LangGraph handles error boundaries natively
3. **Interrupt/resume** — `interrupt()` primitive maps perfectly to the Ctrl+C pause -> guidance injection -> resume flow
4. **Development velocity** — 4 graph nodes + 1 state class vs. hundreds of lines of custom event loop code
5. **Observability** — LangGraph's built-in tracing makes debugging multi-step chains trivial compared to raw asyncio logs

The dependency footprint (~2MB) is negligible relative to the value of not building a state machine from scratch.

## Consequences

### Positive
- State management is declarative — adding a new pipeline node is a 5-line function + 1 graph edge
- Parallel subagents "just work" via subgraphs with automatic result collection
- All state transitions are logged for free, enabling session replay
- Supervisor can inject guidance at any node without race conditions

### Negative
- LangGraph API churn (v0.x -> v1.x migration required effort)
- Opaque error messages when graph compilation fails
- Harder to reason about performance (graph traversal overhead vs. raw loop)
- Lock-in to LangChain ecosystem (mitigated by thin abstraction layer in `state.py`)

## Alternatives Not Pursued

- **Celery task queue**: Overkill for single-machine agent; adds Redis/RabbitMQ dependency
- **Temporal.io**: Enterprise-grade but requires external server; poor fit for local security tool
- **Prefect**: Data pipeline focus; doesn't map cleanly to LLM agent loops
