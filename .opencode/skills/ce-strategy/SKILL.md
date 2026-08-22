---
name: ce-strategy
description: Create or maintain STRATEGY.md - the upstream anchor document that guides ideation, brainstorming, and planning. Strategy choices flow into every feature decision.
---

# /ce-strategy

## Description

Create or maintain `STRATEGY.md` - the upstream anchor document that guides ideation, brainstorming, and planning. Strategy choices flow into every feature decision.

## When to Use

- At project inception to establish strategic direction
- When strategy choices need to be documented and shared
- Before `/ce-ideate` or `/ce-brainstorm` to provide grounding
- When revisiting strategic decisions after market/context changes

## Input

- **project-context**: Current project state, goals, and constraints
- **market-analysis**: External market conditions and competitive landscape
- **stakeholder-input**: Feedback from users, customers, and team members

## Output

- **File**: `STRATEGY.md` in project root
- **Format**: Markdown with strategic pillars, key decisions, and success metrics

## Configuration

```yaml
ce-strategy:
  template: "standard"  # standard | minimal | comprehensive
  include-market-analysis: true
  include-stakeholder-map: true
  auto-update: false  # Whether to auto-update based on new inputs
```

## allowed-tools

- Read: Read existing project files and documentation
- Write: Create/update STRATEGY.md
- Grep: Search for strategic references in codebase
- Glob: Find relevant project files

## Prompt

```
<strategy-creation>
Create or update STRATEGY.md for this project. The strategy document serves as the upstream anchor for all ideation, brainstorming, and planning.

Key sections to include:
1. **Vision** - What are we building and why?
2. **Strategic Pillars** - 3-5 core principles that guide decisions
3. **Target Audience** - Who are we serving?
4. **Key Metrics** - How will we measure success?
5. **Competitive Positioning** - How do we differentiate?
6. **Risks and Mitigations** - Key strategic risks
7. **Decisions Log** - Key decisions with rationale and dates

<strategy-principles>
- Strategy captures WHAT (direction, goals, priorities) not HOW (implementation details)
- Decisions should be traceable to strategic pillars
- Include explicit out-of-scope items to prevent scope creep
- Document assumptions that, if wrong, would change the strategy
</strategy-principles>

<grounding>
Read existing project context:
- README.md and project documentation
- Existing STRATEGY.md if present
- Recent commits and issues
- Market analysis or competitive research
</grounding>
</strategy-creation>

<output-format>
STRATEGY.md with the following structure:
```
# Strategy: {Project Name}

## Vision
{vision statement}

## Strategic Pillars
1. {pillar 1}
2. {pillar 2}
3. {pillar 3}

## Target Audience
{audience description}

## Key Metrics
- {metric 1}
- {metric 2}

## Competitive Positioning
{positioning statement}

## Risks and Mitigations
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| {risk} | {high/med/low} | {high/med/low} | {mitigation} |

## Decisions Log
| Date | Decision | Rationale | Strategic Pillar |
|------|----------|-----------|------------------|
| {date} | {decision} | {rationale} | {pillar} |

## Out of Scope
- {item 1}
- {item 2}
```
</output-format>

## Examples

### Create initial strategy:
```
/ce-strategy
```

### Update strategy with new market data:
```
/ce-strategy --update --input "new competitor analysis"
```

### Minimal strategy for small project:
```
/ce-strategy --template minimal
```

## Notes

- STRATEGY.md is read as grounding by `/ce-ideate`, `/ce-brainstorm`, and `/ce-plan`
- Strategy decisions should be revisited quarterly or after major market changes
- Use `(session-settled: user-directed)` annotations for decisions that carry forward