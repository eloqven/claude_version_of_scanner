---
name: ce-dogfood
description: Use the product yourself to find issues before users do. Runs through common workflows and reports friction points.
---

# /ce-dogfood

## Description

Use the product yourself to find issues before users do. Runs through common workflows and reports friction points.

## When to Use

- Before releasing a feature
- When you suspect UX issues
- After `/ce-work` to validate implementation
- Before `/ce-code-review` to catch usability issues

## Input

- **feature**: Feature to dogfood
- **scenarios**: User scenarios to test
- **duration**: How long to spend dogfooding

## Output

- **File**: `docs/dogfood/dogfood-{timestamp}.md`
- **Issues**: Friction points and bugs found

## Configuration

```yaml
ce-dogfood:
  duration: "15m"  # Time to spend dogfooding
  scenarios: "common"  # common | all | custom
  record-session: false  # Whether to record the session
```

## allowed-tools

- Bash: Run the product
- Read: Read documentation
- Write: Document findings

## Prompt

```
<dogfooding>
Use the product yourself to find issues before users do.

<dogfooding-process>
1. Identify common user workflows
2. Execute each workflow as a user would
3. Note friction points, confusion, and bugs
4. Document findings with evidence
5. Prioritize issues by impact
</dogfooding-process>

<scenario-types>
- Common: Most frequent user workflows
- All: All documented workflows
- Custom: User-specified scenarios
</scenario-types>

<issue-tracking>
For each issue found:
- Description: What went wrong or was confusing
- Steps: How to reproduce
- Impact: How much this affects users
- Severity: P0-P3
- Suggestion: How to fix
</issue-tracking>
</dogfooding>

<output-format>
Dogfooding report:
```
docs/dogfood/dogfood-{timestamp}.md
├── feature: <what was dogfooded>
├── duration: <time spent>
├── scenarios-tested:
│   ├── {scenario}: {pass|fail|friction}
│   └── ...
├── issues-found:
│   ├── {n}:
│   │   ├── description: <what went wrong>
│   │   ├── steps: <how to reproduce>
│   │   ├── impact: <user impact>
│   │   ├── severity: <P0-P3>
│   │   └── suggestion: <how to fix>
│   └── ...
├── summary: {issues-found} issues, {high-impact} high impact
└── recommendations: <next steps>
```
</output-format>

## Examples

### Basic dogfooding:
```
/ce-dogfood "new checkout flow"
```

### Extended dogfooding:
```
/ce-dogfood --duration 30m --scenarios all "dashboard redesign"
```

### Custom scenarios:
```
/ce-dogfood --scenarios "login,search,checkout" "new auth system"
```

## Notes

- Catches UX issues before users do
- Integrates with /ce-code-review for technical review
- Stored in docs/dogfood/ for reference
- Helps prioritize user-facing issues