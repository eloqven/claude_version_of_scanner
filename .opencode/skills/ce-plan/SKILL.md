---
name: ce-plan
description: Enrich feature ideas or requirements-only plans into implementation-ready plans with structured guardrails, U-IDs, and origin tracing.
---

# /ce-plan

## Description

Enrich feature ideas or requirements-only plans into implementation-ready plans with structured guardrails, U-IDs, and origin tracing.

## When to Use

- After `/ce-brainstorm` when you have a requirements-only unified plan
- When you need to convert ideas into actionable implementation plans
- Before `/ce-work` to ensure plan readiness and guardrail compliance

## Prompt

```
<plan-enrichment>
Take the requirements-only unified plan from /ce-brainstorm and enrich it into an implementation-ready plan.

For each requirement, provide:
- U-ID: A stable identifier that survives plan reordering, splitting, and deletion
- Acceptance criteria (given/when/then format)
- Priority (Must/Should/Could/Won't)
- Dependencies on other U-IDs
- Risk level (P0-P3)
- Autofix class (if applicable)

<confidence-check>
Rate your confidence in this plan on a scale of 1-5. If confidence is 3 or below, we will auto-deepen the weakest sections by running sub-agents to strengthen them before proceeding.
</confidence-check>

<origin-tracing>
Trace all R/A/F/AE IDs back to the /ce-brainstorm session:
- Each requirement must reference its originating R/A/F/AE ID
- Identify any IDs that cannot be traced - these are gaps from the brainstorm phase
- Document why certain IDs could not be carried forward
</origin-tracing>

<guardrails>
The plan must capture WHAT (decisions, scope, units, tests, risks) but NOT HOW (code, signatures, step-by-step implementation). The execution details will be handled in /ce-work.

Plan sections required:
1. Summary - High-level overview from brainstorm
2. Requirements - Enriched with U-IDs, priorities, dependencies
3. Test scenarios - What constitutes success/failure for each requirement
4. Risks and mitigations - Identified with severity P0-P3
5. Out of scope - Explicitly stated to prevent scope creep
6. Definition of done - Checklist for when /ce-work can begin
</guardrails>
</plan-enrichment>

<output-format>
A structured plan document in the following format:

```
plan-{timestamp}.md
├── summary: <from brainstorm>
├── requirements:
│   ├── {u-id-1}:
│   │   ├── acceptance-criteria: "<given/when/then>"
│   │   ├── priority: "Must/Should/Could/Won't"
│   │   ├── dependencies: ["{u-id-x}"]
│   │   ├── risk: "P0/P1/P2/P3"
│   │   └── autofix-class: "X" (if applicable)
│   └── ...
├── test-scenarios: [...]
├── risks: [...]
├── out-of-scope: [...]
└── definition-of-done: [...]
```
</output-format>