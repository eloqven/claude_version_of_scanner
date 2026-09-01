---
name: ce-code-review
description: Report-only multi-agent review against the plan before merging; local apply is explicit. Uses skill-local reviewer personas with core and conditional reviewers.
---

# /ce-code-review

## Description

Report-only multi-agent review against the plan before merging; local apply is explicit. Uses skill-local reviewer personas with core and conditional reviewers.

## When to Use

- After `/ce-work` and before `/ce-compound`
- When PR is ready for merge and needs formal review
- For calibrating judgment and catching patterns, not just bugs

## Input

- **diff**: The code diff to review (from git diff or PR)
- **plan-file**: Path to the implementation plan from `/ce-plan` (optional but recommended for U-ID tracing)
- **u-ids**: List of U-IDs to focus the review on (optional)
- **changed-files**: List of files changed (auto-detected from diff if not provided)

## Output

- **File**: `docs/residual-review-findings/review-{timestamp}.md`
- **Format**: Structured markdown report with findings, severity, and recommendations
- **Console**: Summary of findings with severity counts

## Configuration

```yaml
ce-code-review:
  severity_threshold: "P2"  # Minimum severity to report (P0/P1/P2/P3)
  auto_apply_class_a: false  # Whether to auto-apply Class A autofixes
  reviewer_selection: "auto"  # auto | core-only | all
  max_findings_per_reviewer: 10  # Limit findings per reviewer persona
  include_conditional_reviewers: true  # Enable conditional reviewers
```

## allowed-tools

- Bash: `git diff`, `git log`, `git show` for diff analysis
- Grep: Search for patterns in changed files
- Read: Read file contents for detailed review
- Glob: Find files matching patterns
- Task: Dispatch subagent reviewers for multi-agent review

## Prompt

```
<review-personas>
Core reviewers (always applied based on changed areas):
1. **Correctness** - Verifies logic, edge cases, and behavioral correctness against plan requirements
2. **Project standards** - Ensures code conforms to project conventions, naming, patterns

Conditional reviewers (applied when specific areas change):
3. **Testing** - When tests/test-harnesses change: verifies test coverage, coherence, and no regressions
4. **Security** - When auth, data validation, or input sanitization changes: threat model check, OWASP basics
5. **Data migration** - When schema/changing data structures: migration path, backward compatibility, data loss prevention
6. **Performance** - When critical paths, algorithms, or resource usage changes: profiling evidence, not micro-optimizations

<subagent-dispatch>
For multi-agent review, dispatch each reviewer persona as a separate subagent:
- Each subagent receives the diff and plan requirements
- Each subagent applies its specific lens (correctness, testing, security, etc.)
- Results are aggregated into a unified report
- Subagents run in parallel for efficiency
</subagent-dispatch>

<review-format>
Each reviewer provides:
- Verdict: PASS/FAIL/NIT (nitpick)
- Specific findings with evidence from the diff
- Relation to plan requirements (U-ID references)
- Autofix class if NIT (minor) or FAIL (blocking)
- Severity: P0 (blocks merge) - P3 (cosmetic/informational)

<origin-tracing>
All findings must trace back to plan requirements via U-IDs. Findings not traceable to a requirement must be flagged as "out-of-scope" or "assumption-based."
</origin-tracing>

<severity-calibration>
P0: Critical - blocks merge, requires autofix
P1: High - blocks merge, no autofix needed but must be addressed
P2: Medium - can merge with follow-up, autofix nice-to-have
P3: Low - cosmetic, no action required unless accumulated
</severity-calibration>

<autofix-class>
Class A: Automatic - can be applied by tooling without introducing risk
Class B: Semiautomatic - requires human review of automated changes
Class C: Manual - must be done by developer, no automation
</autofix-class>

<console-output>
After completing the review, output the results in this exact format to the console:

## Code Review Complete

**Verdict: {PASS|FAIL} / {Merge|Hold|Rework}**

| Reviewer | Type | Verdict | Severity |
|---|---|---|---|
| Correctness | Core | {PASS|FAIL|NIT} | {P0|P1|P2|P3} |
| Project standards | Core | {PASS|FAIL|NIT} | {P0|P1|P2|P3} |
| Testing | Conditional | {PASS|FAIL|NIT|N/A} | {P0|P1|P2|P3|N/A} |
| Security | Conditional | {PASS|FAIL|NIT|N/A} | {P0|P1|P2|P3|N/A} |
| Data migration | Conditional | {PASS|FAIL|NIT|N/A} | {P0|P1|P2|P3|N/A} |
| Performance | Conditional | {PASS|FAIL|NIT|N/A} | {P0|P1|P2|P3|N/A} |

**Summary:** {brief summary of findings}

**Recommendation:** {Merge|Hold|Rework} - {one sentence reason}

**Report saved to:** `docs/residual-review-findings/review-{timestamp}.md`
</console-output>

<file-output>
Save the full detailed report to `docs/residual-review-findings/review-{timestamp}.md` with:
```
review-{timestamp}.md
├── summary: <overall verdict and severity>
├── findings:
│   ├── {u-id-reference}:
│   │   ├── reviewer: "<persona>"
│   │   ├── verdict: "PASS/FAIL/NIT"
│   │   ├── severity: "P0/P1/P2/P3"
│   │   ├── finding: "<description with diff evidence>"
│   │   ├── autofix-class: "A/B/C" (if applicable)
│   │   └── related-requirement: "<requirement description>"
│   └── ...
├── autofix-actions: [...]
└── recommendation: "merge/hold/rework"
```
</file-output>
</review-format>

## Examples

### Basic usage (auto-detect diff and plan):
```
/ce-code-review
```

### Review specific U-IDs:
```
/ce-code-review --u-ids R-001, R-002, R-003
```

### Review with explicit plan file:
```
/ce-code-review --plan-file docs/plans/plan-2026-08-14.md
```

### Core reviewers only (skip conditional):
```
/ce-code-review --reviewer-selection core-only
```

### Auto-apply Class A fixes:
```
/ce-code-review --auto-apply-class-a
```

## Notes

- This skill is **report-only** - it does not modify code
- Local apply of autofixes is explicit and requires user confirmation
- Review findings are saved to `docs/residual-review-findings/`
- The skill reads existing solutions from `docs/solutions/` as grounding
- Conditional reviewers are automatically selected based on changed file types and patterns