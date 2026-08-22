---
name: ce-ideate
description: Generate and critically evaluate grounded ideas. When you don't yet know what to build, ideate first, then carry the strongest survivor into /ce-brainstorm.
---

# /ce-ideate

## Description

Generate and critically evaluate grounded ideas. When you don't yet know what to build, ideate first, then carry the strongest survivor into `/ce-brainstorm`.

## When to Use

- When you have a problem but no clear direction
- Before `/ce-brainstorm` to generate candidate ideas
- When exploring new feature areas or product directions
- When stuck in analysis paralysis

## Input

- **problem-statement**: The problem or opportunity to explore
- **constraints**: Known constraints (technical, business, timeline)
- **context**: Project context, user needs, market conditions

## Output

- **File**: `docs/ideas/ideas-{timestamp}.md`
- **Format**: Ranked list of grounded ideas with evaluation criteria

## Configuration

```yaml
ce-ideate:
  num-ideas: 5  # Number of ideas to generate
  depth: "standard"  # shallow | standard | deep
  include-past-learnings: true  # Read docs/solutions/ as grounding
  include-web-research: true  # Research past art on the web
```

## allowed-tools

- Read: Read existing project files and docs/solutions/
- Grep: Search codebase for relevant patterns
- WebSearch: Research past art and competitive solutions
- Glob: Find relevant project files

## Prompt

```
<idea-generation>
Generate and critically evaluate grounded ideas for: {{problem-statement}}

<grounding>
First, do the homework:
1. Read existing project context (README, docs, recent code)
2. Read past learnings from docs/solutions/
3. Research past art on the web (competitors, similar solutions)
4. Optionally check issue tracker for related problems
</grounding>

<idea-generation-process>
Generate {{num-ideas}} ideas, then critically evaluate each using:
1. **Evidence** - What evidence supports this idea's viability?
2. **Specificity** - Is this specific enough to be actionable?
3. **Feasibility** - Can we actually build this given constraints?
4. **Impact** - What's the potential impact if successful?
5. **Effort** - What's the estimated effort to implement?

Rank ideas by a composite score of impact × feasibility / effort.
</idea-generation-process>

<critical-evaluation>
For each idea, apply the named gap lenses:
- Evidence: What supports this premise?
- Specificity: Is it testable and implementable?
- Counterfactual: What would need to be different for this to not work?
- Attachment: Are we biased toward this approach?
- Durability: Will this still make sense in 6 months?
</critical-evaluation>
</idea-generation>

<output-format>
Ranked ideas document:
```
docs/ideas/ideas-{timestamp}.md
├── problem-statement: <from input>
├── context: <project context summary>
├── ideas:
│   ├── {rank}. {idea-name}:
│   │   ├── description: <what this idea does>
│   │   ├── evidence: <supporting evidence>
│   │   ├── impact: <high/medium/low>
│   │   ├── effort: <high/medium/low>
│   │   ├── feasibility: <high/medium/low>
│   │   ├── score: <impact × feasibility / effort>
│   │   ├── risks: <key risks>
│   │   └── next-step: <how to validate this idea>
│   └── ...
├── recommendation: <strongest idea to carry into /ce-brainstorm>
└── rejected-ideas: <brief notes on why other ideas were rejected>
```
</output-format>

## Examples

### Basic ideation:
```
/ce-ideate new authentication methods for mobile app
```

### Deep ideation with web research:
```
/ce-ideate --depth deep --include-web-research "improve checkout conversion"
```

### Ideate from open issues:
```
/ce-ideate open issues
```

## Notes

- The strongest idea is automatically routed into `/ce-brainstorm`
- Ideas are grounded in codebase, past learnings, and web research
- Use `--num-ideas N` to control how many ideas to generate