---
name: ce-debug
description: Reproduce failures, trace root cause, fix bugs, and prepare non-trivial fixes for PR. When you start from broken behavior instead of a feature.
---

# /ce-debug

## Description

Reproduce failures, trace root cause, fix bugs, and prepare non-trivial fixes for PR. When you start from broken behavior instead of a feature.

## When to Use

- When you have a bug report or failing behavior
- When you need to trace root cause through multiple layers
- When the fix requires non-trivial changes
- Before `/ce-code-review` to ensure the fix is solid

## Input

- **bug-description**: Description of the failing behavior
- **reproduction-steps**: Steps to reproduce (if known)
- **error-messages**: Any error messages or logs

## Output

- **File**: `docs/debugging/debug-{timestamp}.md`
- **Code**: Fix implementation

## Configuration

```yaml
ce-debug:
  root-cause-depth: "deep"  # shallow | standard | deep
  include-fix-verification: true  # Whether to verify the fix
  include-regression-tests: true  # Whether to add regression tests
```

## allowed-tools

- Read: Read code and logs
- Grep: Search for error patterns
- Bash: Run tests, reproduce bugs, git operations
- Glob: Find relevant files

## Prompt

```
<debug-process>
Debug the following issue: {{bug-description}}

<debug-phases>
1. **Reproduce** - Confirm the bug exists and document reproduction steps
2. **Trace** - Trace the root cause through the code
3. **Hypothesize** - Form hypotheses about the root cause
4. **Verify** - Test hypotheses with experiments
5. **Fix** - Implement the fix
6. **Verify Fix** - Confirm the fix resolves the issue
7. **Regression Tests** - Add tests to prevent recurrence
</debug-phases>

<root-cause-analysis>
Use the "5 whys" technique to trace to root cause:
- Why did this happen? (symptom)
- Why did that happen? (immediate cause)
- Why did that happen? (deeper cause)
- Why did that happen? (systemic issue)
- Why did that happen? (root cause)

Document each layer and the evidence supporting it.
</root-cause-analysis>
</debug-process>

<output-format>
Debug report:
```
docs/debugging/debug-{timestamp}.md
├── bug-description: <from input>
├── reproduction:
│   ├── steps: <how to reproduce>
│   ├── expected: <expected behavior>
│   └── actual: <actual behavior>
├── root-cause-analysis:
│   ├── layer-1: <symptom> - <evidence>
│   ├── layer-2: <immediate cause> - <evidence>
│   ├── layer-3: <deeper cause> - <evidence>
│   ├── layer-4: <systemic issue> - <evidence>
│   └── layer-5: <root cause> - <evidence>
├── fix:
│   ├── approach: <how the fix addresses root cause>
│   ├── files-changed: <list of files>
│   └── verification: <how fix was verified>
├── regression-tests: <tests added to prevent recurrence>
└── learnings: <key insights for future debugging>
```
</output-format>

## Examples

### Basic debug:
```
/ce-debug "the checkout webhook sometimes creates duplicate invoices"
```

### Deep root cause analysis:
```
/ce-debug --root-cause-depth deep "API returns 500 on large payloads"
```

### Debug with reproduction steps:
```
/ce-debug --reproduction-steps "1. Login 2. Upload CSV 3. Click process" "CSV upload fails silently"
```

## Notes

- This skill replaces the standard brainstorm→plan→work flow when input is a bug
- After fixing, proceed to `/ce-code-review` and `/ce-compound`
- Root cause analysis should go beyond symptoms to systemic issues
- Regression tests are critical for preventing recurrence