---
name: ce-explain
description: Turn a concept, a diff, an idea, or "what did I do this week?" into a dense, self-contained visual document built to keep. Optionally includes a check-in (predict-then-reveal for diffs, corrected exercises) when the material is worth retaining.
---

# /ce-explain

## Description

Turn a concept, a diff, an idea, or "what did I do this week?" into a dense, self-contained visual document built to keep. Optionally includes a check-in (predict-then-reveal for diffs, corrected exercises) when the material is worth retaining.

## When to Use

- To document a complex concept or system for future reference
- To explain a diff or PR to stakeholders
- To create visual documentation that lasts
- When you need to "teach" something to your future self

## Input

- **topic**: The concept, diff, idea, or work period to explain
- **audience**: Target audience (engineer, stakeholder, new hire, etc.)
- **format**: Output format (visual, text, hybrid)

## Output

- **File**: `docs/explanations/explain-{topic}-{timestamp}.md`
- **Format**: Dense, self-contained visual document

## Configuration

```yaml
ce-explain:
  audience: "engineer"  # engineer | stakeholder | new-hire | general
  format: "visual"  # visual | text | hybrid
  include-check-in: false  # Whether to include predict-then-reveal exercises
  depth: "standard"  # shallow | standard | deep
```

## allowed-tools

- Read: Read code, diffs, and documentation
- Grep: Search codebase for relevant patterns
- Glob: Find relevant files
- Bash: Run git commands for diff analysis

## Prompt

```
<explanation-creation>
Create a dense, self-contained visual document explaining: {{topic}}

<document-principles>
- Built to keep: This document should be useful 6 months from now
- Self-contained: No external context needed to understand
- Visual: Use diagrams, tables, and visual structures
- Dense: Pack maximum information per page
- Actionable: Include concrete takeaways and next steps
</document-principles>

<content-structure>
1. **TL;DR** - One-sentence summary
2. **Context** - Why this matters, who cares
3. **Core Concept** - The main idea, explained simply
4. **Deep Dive** - Technical details, architecture, code examples
5. **Visual Aids** - Diagrams, flowcharts, tables
6. **Key Takeaways** - Actionable insights
7. **Further Reading** - Links to related docs/concepts
</content-structure>

<check-in-mode>
If include-check-in is enabled:
- For diffs: Predict what the change does, then reveal the actual implementation
- For concepts: Test understanding with corrected exercises
- For work periods: Summarize accomplishments and lessons learned
</check-in-mode>
</explanation-creation>

<output-format>
Explanation document:
```
docs/explanations/explain-{topic}-{timestamp}.md
├── tldr: <one-sentence summary>
├── context: <why this matters>
├── core-concept: <main idea explained simply>
├── deep-dive: <technical details>
├── visual-aids: <diagrams, tables, charts>
├── key-takeaways: <actionable insights>
└── further-reading: <related links>
```
</output-format>

## Examples

### Explain a concept:
```
/ce-explain the Fibonacci confluence matrix algorithm
```

### Explain a diff with check-in:
```
/ce-explain --include-check-in "the checkout webhook refactoring"
```

### Explain for stakeholders:
```
/ce-explain --audience stakeholder --format visual "why we're migrating to microservices"
```

## Notes

- Documents are stored in docs/explanations/ and indexed for future reference
- Visual format uses ASCII diagrams, tables, and structured layouts
- Check-in mode helps validate understanding and retention