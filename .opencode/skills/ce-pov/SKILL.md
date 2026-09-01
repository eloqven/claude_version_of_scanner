---
name: ce-pov
description: Form a decisive, project-grounded POV (point of view) on an adoption, document, or approach set. Optionally cross-checked by named peers or oracle through a blind initial round and bounded reconciliation.
---

# /ce-pov

## Description

Form a decisive, project-grounded POV (point of view) on an adoption, document, or approach set. Optionally cross-checked by named peers or `oracle` through a blind initial round and bounded reconciliation.

## When to Use

- Before committing to a technology, framework, or approach
- When evaluating multiple options for a decision
- Before major architectural decisions
- When you need a decisive verdict, not just analysis

## Input

- **topic**: The adoption, document, or approach to evaluate
- **options**: List of options to compare (if not provided, generate them)
- **criteria**: Evaluation criteria (if not provided, derive from project context)

## Output

- **File**: `docs/povs/pov-{topic}-{timestamp}.md`
- **Format**: Decisive verdict with rationale, trade-offs, and recommendations

## Configuration

```yaml
ce-pov:
  cross-check: false  # Whether to cross-check with peers/oracle
  cross-check-rounds: 2  # Number of blind reconciliation rounds
  depth: "standard"  # shallow | standard | deep
```

## allowed-tools

- Read: Read project context and documentation
- Grep: Search codebase for relevant patterns
- WebSearch: Research options and past art
- Glob: Find relevant project files

## Prompt

```
<pov-formation>
Form a decisive, project-grounded POV on: {{topic}}

<evaluation-framework>
Evaluate each option against these criteria:
1. **Alignment** - How well does this align with project strategy and goals?
2. **Feasibility** - Can we implement this given our constraints?
3. **Risk** - What are the risks of this approach?
4. **Maintainability** - How easy will this be to maintain long-term?
5. **Performance** - What are the performance implications?
6. **Team fit** - Does this match our team's skills and preferences?

<blind-cross-check>
If cross-check is enabled:
1. First round: Evaluate options independently without seeing others' opinions
2. Second round: Reconcile differences with bounded discussion
3. Final verdict: Document the converged position
</blind-cross-check>
</evaluation-framework>

<decision-framework>
Make a DECISIVE choice - not just analysis. The POV should clearly state:
- The recommended approach
- Why it's the best choice
- What we're giving up by not choosing alternatives
- What would need to change for a different choice to be better
</decision-framework>
</pov-formation>

<output-format>
POV document:
```
docs/povs/pov-{topic}-{timestamp}.md
├── verdict: "ADOPT / TRIAL / ASSESS / HOLD"
├── recommended-approach: <the chosen option>
├── rationale: <why this is the best choice>
├── trade-offs:
│   ├── accepted: <what we're giving up>
│   └── rejected: <why alternatives were not chosen>
├── criteria-scores:
│   ├── {option-1}:
│   │   ├── alignment: <score 1-5>
│   │   ├── feasibility: <score 1-5>
│   │   ├── risk: <score 1-5>
│   │   ├── maintainability: <score 1-5>
│   │   └── performance: <score 1-5>
│   └── ...
├── confidence: <1-5 rating>
├── conditions: <what would change the verdict>
└── next-steps: <immediate actions>
```
</output-format>

## Examples

### Basic POV:
```
/ce-pov adopt TypeScript for new frontend project
```

### POV with cross-check:
```
/ce-pov --cross-check --cross-check-rounds 3 "migrate from MongoDB to PostgreSQL"
```

### POV with custom criteria:
```
/ce-pov --criteria "cost, team-skill-match, migration-effort" "use Docker vs Kubernetes"
```

## Notes

- The verdict should be decisive: ADOPT, TRIAL, ASSESS, or HOLD
- Cross-check uses blind initial round + bounded reconciliation
- POVs are stored in docs/povs/ and read as grounding by future decisions