---
name: orbstack-gemini-semantic-tests
description: Use when the user wants meaningful OrbStack/Gemini CLI experiments instead of shallow smoke checks. This skill defines a multi-layer semantic test workflow: inspect OrbStack runtime, inspect pipeline/control-plane state, run Gemini CLI in read-only mode against that evidence, write a concise experiment report, then commit the report so the commit-driven pipeline can pick it up.
---

# OrbStack Gemini Semantic Tests

Use this skill when the user wants **meaningful tests**, not just "does the container start?" checks.

## What counts as meaningful

A good test must touch at least **three layers**:

1. **Runtime layer** — OrbStack, docker context, running lab/container state
2. **Semantic/control layer** — Gemini CLI read-only analysis over real workspace + lab context
3. **Commit/pipeline layer** — write a report and commit it so the commit-driven pipeline can react

Do **not** stop at health checks, version output, or "it booted".

## Workflow

1. **Define one hypothesis.** Example: "The current OrbStack-backed workflow is ready for repeated Gemini CLI semantic investigations without depending on manual FUSE setup."
2. **Collect real evidence.** Check OrbStack/docker state, pipeline status, active mount mode, lab container status, and relevant repo paths.
3. **Run Gemini CLI in read-only mode.** Use `--approval-mode plan` and include both the repo and OrbStack lab directories.
4. **Write a concise report.** The report should say what was tested, what evidence exists, what Gemini concluded, and what next test is most valuable.
5. **Commit the report.** This is part of the workflow, not an optional extra.

## Standard command

```bash
bash .github/skills/orbstack-gemini-semantic-tests/scripts/run-orbstack-semantic-experiment.sh
```

## Output contract

The generated report should include:

- hypothesis
- runtime evidence
- semantic analysis result
- verdict
- next meaningful test

Default output path:

```text
.github/skills/orbstack-gemini-semantic-tests/LATEST_REPORT.md
```

## Rules

1. Prefer **real evidence** over narrative.
2. Keep Gemini in **read-only / plan** mode for experiments.
3. Do not include secrets, env dumps, or API keys in the report.
4. After generating the report, **commit and push** it on a dedicated branch.
