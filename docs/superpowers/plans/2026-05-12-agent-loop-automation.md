# Agent Loop Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local automation loop that lets ClaudeCode implement, Codex review, and ClaudeCode fix until Codex approves.

**Architecture:** Keep ACP/BMAD as the source of truth and add a PowerShell runner under `.agent-collab/tools/`. The runner invokes configurable local CLI commands, waits for append-only ACP files, and records state and final summaries under `.agent-collab/state/`.

**Tech Stack:** PowerShell, Markdown ACP records, BMAD file artifacts.

---

### Task 1: Add Orchestrator Smoke Test

**Files:**
- Create: `.agent-collab/tools/test-run-agent-loop.ps1`

- [x] **Step 1: Write a failing smoke test**

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .agent-collab/tools/test-run-agent-loop.ps1
```

Expected before implementation: failure because `.agent-collab/tools/run-agent-loop.ps1` does not exist.

- [x] **Step 2: Cover dry-run and mock loop behavior**

The test asserts dry-run state exists, then uses mock Claude/Codex PowerShell commands to create `DEV_DONE` and `REVIEW: APPROVED`.

### Task 2: Implement Local Loop Runner

**Files:**
- Create: `.agent-collab/tools/run-agent-loop.ps1`

- [x] **Step 1: Add configurable command entry**

Support `-Task`, `-FromNextAction`, `-ClaudeCommand`, `-ClaudeArgs`, `-CodexCommand`, `-CodexArgs`, `-MaxRounds`, `-DryRun`, and `-SkipCoordinator`.

- [x] **Step 2: Add event waiting and decision routing**

Wait for `DEV_DONE` / `FIX_DONE`, call Codex review, route `CHANGES_REQUESTED` / `BLOCKED` back to Claude, and stop on `APPROVED`.

- [x] **Step 3: Record loop state**

Write `active-loop.json`, prompt files, command logs, and `last-loop-summary.md`.

### Task 3: Document Both Entry Points

**Files:**
- Modify: `.agent-collab/tools/README.md`
- Modify: `.agent-collab/AGENT_PROTOCOL.md`
- Modify: `.agent-collab/WORKFLOW_ADAPTER.md`
- Modify: `.agent-collab/AGENT_BOOTSTRAP.md`

- [x] **Step 1: Document direct PowerShell usage**

Show `run-agent-loop.ps1 -Task` and `run-agent-loop.ps1 -FromNextAction`.

- [x] **Step 2: Document natural-language startup**

Tell agents that "启动自动协作任务" maps to the local runner command.

### Task 4: Verify

**Files:**
- Read: `.agent-collab/tools/run-agent-loop.ps1`
- Read: `.agent-collab/tools/test-run-agent-loop.ps1`

- [x] **Step 1: Run smoke test**

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .agent-collab/tools/test-run-agent-loop.ps1
```

Expected: `PASS run-agent-loop smoke test`.
