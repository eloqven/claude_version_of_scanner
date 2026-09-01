---
name: ce-work
description: Execute implementation-ready plans natively or through a qualified cross-model author while retaining host verification, commits, and shipping.
---

# /ce-work

## Description

Execute implementation-ready plans natively or through a qualified cross-model author while retaining host verification, commits, and shipping.

## When to Use

- After `/ce-plan` when you have an implementation-ready plan
- When starting actual code implementation work
- For both native execution (within the same model) and cross-model author scenarios

## Prompt

```
<plan-execution>
Execute the plan produced by /ce-plan. For each requirement/U-ID:

1. Begin work on the first unstarted U-ID
2. Work until completion or until blocked
3. If blocked, document the blocker and move to the next U-ID
4. At each checkpoint, verify acceptance criteria are being met

<native-execution>
When executing natively:
- Retain host verification of all changes
- Track durable progress across interruptions
- Transactional commits - either commit the full U-ID change or revert
- Ship only when definition-of-done from /ce-plan is met
</native-execution>

<cross-model-author>
When using a cross-model author:
- The host model verifies all code changes before acceptance
- Cross-model author writes code, host model reviews each chunk
- Host retains final commit and shipping authority
- All changes must pass host verification before proceeding to next U-ID
</cross-model-author>

<durable-progress>
Progress is tracked in a work log with:
- U-ID status: not-started, in-progress, completed, blocked, cancelled
- Time spent per U-ID
- Blockers documented with unblocking actions
- Checkpoint reviews against acceptance criteria
</durable-progress>

<commit-shipping>
At definition-of-done:
1. Run final acceptance test suite
2. Create git commit with clear message (scope, type, breaking changes)
3. Push to remote
4. Open PR /ce-code-review
5. Only after PR approval, proceed to /ce-compound
</commit-shipping>
</plan-execution>

<output-format>
Work log entry per U-ID:
```
{u-id-status}: {u-id}
  - Started: <timestamp>
  - Completed: <timestamp> (or "blocked: <reason>")
  - Acceptance criteria met: <yes/no>
  - Work log: <brief description of what was done>
  - Blockers: <if applicable>
```
</output-format>