---
name: ce-doc-review
description: Review and improve documentation for clarity, completeness, and accuracy. Ensures docs are useful and up-to-date.
---

# /ce-doc-review

## Description

Review and improve documentation for clarity, completeness, and accuracy. Ensures docs are useful and up-to-date.

## When to Use

- After major feature changes
- Before releasing documentation
- When docs are unclear or outdated
- Before `/ce-compound` to capture documentation learnings

## Input

- **target**: Documentation to review (file, directory, or "all")
- **focus**: What to focus on (clarity, completeness, accuracy)
- **audience**: Target audience for the docs

## Output

- **File**: `docs/doc-reviews/review-{timestamp}.md`
- **Docs**: Improved documentation

## Configuration

```yaml
ce-doc-review:
  focus: "all"  # clarity | completeness | accuracy | all
  audience: "engineer"  # engineer | user | stakeholder | new-hire
  auto-fix: false  # Whether to auto-fix issues
```

## allowed-tools

- Read: Read documentation files
- Write: Update documentation
- Grep: Search for documentation patterns
- Glob: Find documentation files

## Prompt

```
<doc-review>
Review and improve documentation for: {{target}}

<review-criteria>
1. **Clarity** - Is the language clear and jargon-free?
2. **Completeness** - Are all necessary details included?
3. **Accuracy** - Is the information correct and up-to-date?
4. **Structure** - Is the document well-organized?
5. **Examples** - Are there concrete examples?
6. **Audience** - Is it appropriate for the target audience?
</review-criteria>

<review-process>
1. Read the documentation
2. Evaluate against review criteria
3. Identify gaps and issues
4. Suggest improvements
5. Apply fixes if auto-fix is enabled
6. Document findings
</review-process>
</doc-review>

<output-format>
Doc review report:
```
docs/doc-reviews/review-{timestamp}.md
├── target: <documentation reviewed>
├── focus: <clarity|completeness|accuracy|all>
├── findings:
│   ├── {section}:
│   │   ├── issue: <description>
│   │   ├── severity: <P0-P3>
│   │   ├── suggestion: <how to fix>
│   │   └── autofix-class: <A/B/C>
│   └── ...
├── improvements-made: <list of changes>
└── recommendations: <further improvements>
```
</output-format>

## Examples

### Basic doc review:
```
/ce-doc-review README.md
```

### Review all docs:
```
/ce-doc-review --target "docs/"
```

### Focus on accuracy:
```
/ce-doc-review --focus accuracy --target "docs/architecture/"
```

### Auto-fix issues:
```
/ce-doc-review --auto-fix --target "docs/api/"
```

## Notes

- Reviews documentation against multiple criteria
- Can auto-fix clarity and structure issues
- Integrates with /ce-compound for documentation learnings
- Stored in docs/doc-reviews/ for reference