---
name: ce-retune
description: Retune model parameters, prompts, or configurations based on observed behavior. Improves accuracy, reduces errors, or optimizes for specific outcomes.
---

# /ce-retune

## Description

Retune model parameters, prompts, or configurations based on observed behavior. Improves accuracy, reduces errors, or optimizes for specific outcomes.

## When to Use

- When model outputs are inconsistent
- After `/ce-compound` to improve future iterations
- When prompts need refinement
- When model parameters need adjustment

## Input

- **target**: What to retune (prompt, model, config)
- **issue**: Problem to fix (inaccuracy, verbosity, errors)
- **data**: Examples of good/bad outputs

## Output

- **File**: `docs/retunes/retune-{timestamp}.md`
- **Config**: Updated configuration or prompts

## Configuration

```yaml
ce-retune:
  target: "prompt"  # prompt | model | config | agent
  metric: "accuracy"  # accuracy | consistency | speed | cost
  sample-size: 10  # Number of examples to analyze
```

## allowed-tools

- Read: Read current config and examples
- Write: Update config files
- Grep: Search for patterns in outputs

## Prompt

```
<retune-process>
Retune {{target}} to improve {{metric}}.

<analysis>
1. Collect examples of current behavior (good and bad)
2. Identify patterns in failures
3. Form hypotheses about what to change
4. Implement changes
5. Test with new examples
6. Measure improvement
</analysis>

<retune-principles>
- Use data, not intuition
- Make small, incremental changes
- Test thoroughly before deploying
- Document what changed and why
- Track improvement over time
</retune-principles>
</retune-process>

<output-format>
Retune report:
```
docs/retunes/retune-{timestamp}.md
├── target: <what was retuned>
├── metric: <what was improved>
├── issue: <problem being fixed>
├── analysis:
│   ├── examples-analyzed: <count>
│   ├── patterns-found: <list of patterns>
│   └── root-cause: <what caused the issue>
├── changes:
│   ├── {change}: <description>
│   └── ...
├── results:
│   ├── before: <metric value>
│   ├── after: <metric value>
│   └── improvement: <percentage>
└── recommendations: <further tuning opportunities>
```
</output-format>

## Examples

### Retune prompt:
```
/ce-retune --target prompt --metric accuracy "code review findings"
```

### Retune model:
```
/ce-retune --target model --metric consistency "planning agent"
```

### Retune config:
```
/ce-retune --target config --metric speed "build agent"
```

## Notes

- Always use data to guide retuning
- Make small, incremental changes
- Test thoroughly before deploying
- Integrates with /ce-compound for learning capture