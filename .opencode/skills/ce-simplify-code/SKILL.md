---
name: ce-simplify-code
description: Refine freshly written code for clarity and reuse before review. Targets code that keeps slowing changes down, follows the "20% execution / 80% planning" philosophy by tightening recent branch changes.
---

# /ce-simplify-code

## Description

Refine freshly written code for clarity and reuse before review. Targets code that keeps slowing changes down, follows the "20% execution / 80% planning" philosophy by tightening recent branch changes.

## When to Use

- After `/ce-work` when you have fresh code changes
- When one file keeps absorbing unrelated fixes, follow-ups, or merge conflicts
- Before `/ce-code-review` to tighten code quality
- When code churn is high and readability is decreasing

## Prompt

```
<code-simplify>
Take the freshly written code and refine it for clarity, readability, and reuse. Focus on:

1. **Remove duplication** - Extract common patterns, consolidate repeated logic
2. **Improve clarity** - Rename variables/functions for clarity, add docstrings/comments where needed
3. **Reduce complexity** - Simplify nested conditions, extract methods, reduce cyclomatic complexity
4. **Ensure consistency** - Conform to project naming conventions and patterns
5. **Enable reuse** - Identify opportunities for abstraction or extraction into shared utilities

<focus-areas>
Prioritize these in order:
- High: Files with high churn rate (>3 modifications in last 10 commits)
- High: Files flagged by `/ce-compound` as having recurring patterns
- Medium: Functions > 20 lines with multiple responsibilities
- Medium: Complex conditionals without clear guard clauses
- Low: Minor style improvements, formatting adjustments
</focus-areas>

<before-after-example>
Before: A 40-line function with 3 responsibilities, vague variable names `data` and `temp`, and duplicated validation logic.

After: A 22-line function with a single responsibility, descriptive name `validateUserInput()`, and extracted helper functions for each validation concern.
</before-after-example>
</simplify-code>

<output-format>
Refactored code with a brief summary of changes:
```
File: {filename}
Lines changed: {n} added, {m} removed, {c} modified

Summary:
- Extracted {n} helper functions/methods
- Renamed {m} variables/functions for clarity
- Simplified {c} conditional branches
- Added/removed {d} lines of documentation/comments

Key improvements:
- {key improvement 1}
- {key improvement 2}
- {key improvement 3}
```
</output-format>
</code-simplify>