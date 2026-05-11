# Agent Collaboration Protocol Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a reusable agent collaboration protocol that lets Codex, ClaudeCode, and other coding agents coordinate development, review, verification, and architecture decisions.

**Architecture:** The protocol is workflow-agnostic and lives in `.agent-collab/`. Project-specific delivery rules live in `.agent-collab/WORKFLOW_ADAPTER.md`; this repository uses BMAD as its adapter.

**Tech Stack:** Markdown protocol files, append-only mailbox folders, BMAD story artifacts, pytest, mypy, import-linter.

---

### Task 1: Create Protocol Skeleton

**Files:**
- Create: `.agent-collab/AGENT_PROTOCOL.md`
- Create: `.agent-collab/WORKFLOW_ADAPTER.md`
- Create: `.agent-collab/README.md`

- [x] **Step 1: Define universal states, roles, mailbox ownership, and gates**

Write the protocol so every agent can understand when to implement, when to stop, when to request a decision, and when a story is approved.

- [x] **Step 2: Define the BMAD adapter**

Document where BMAD stories live, which files are final sources of truth, and who may update final status.

- [x] **Step 3: Add operator-facing README**

Explain how to onboard another agent and what it must read before work.

### Task 2: Create Roles and Templates

**Files:**
- Create: `.agent-collab/roles/claude-developer.md`
- Create: `.agent-collab/roles/codex-architect-reviewer.md`
- Create: `.agent-collab/templates/dev-done.md`
- Create: `.agent-collab/templates/review.md`
- Create: `.agent-collab/templates/decision-request.md`
- Create: `.agent-collab/templates/decision.md`
- Create: `.agent-collab/templates/verify-result.md`

- [x] **Step 1: Add role files**

Define ClaudeCode as developer and Codex as architect/reviewer/verifier.

- [x] **Step 2: Add message templates**

Require AC coverage, changed files, verification evidence, known shortcuts, findings, and next actions.

### Task 3: Create Mailbox and Log Folders

**Files:**
- Create: `.agent-collab/inbox/codex/.gitkeep`
- Create: `.agent-collab/inbox/claude/.gitkeep`
- Create: `.agent-collab/inbox/other-agents/.gitkeep`
- Create: `.agent-collab/log/decisions/.gitkeep`
- Create: `.agent-collab/log/handoffs/.gitkeep`
- Create: `.agent-collab/log/reviews/.gitkeep`
- Create: `.agent-collab/state/board.md`

- [x] **Step 1: Add append-only mailbox directories**

Each agent writes only to its own mailbox directory.

- [x] **Step 2: Add shared board**

Track active work, window size, and open decisions.

### Task 4: Verify Structure

**Files:**
- Read: `.agent-collab/**`

- [x] **Step 1: List created files**

Run `Get-ChildItem .agent-collab -Recurse` and confirm protocol files exist.

- [x] **Step 2: Review content for ambiguous ownership**

Confirm every writable area has one owner and every gate has a clear approver.

