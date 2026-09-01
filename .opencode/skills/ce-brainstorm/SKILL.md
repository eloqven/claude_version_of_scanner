---
name: ce-brainstorm
description: Interactive Q&A to think through a feature or problem and write a requirements-only unified plan before planning. Uses a one-question-at-a-time blocking approach to prevent diluted answers.
---

# /ce-brainstorm

## Description

Interactive Q&A to think through a feature or problem and write a requirements-only unified plan before planning. Uses a one-question-at-a-time blocking approach to prevent diluted answers.

## When to Use

- At the start of a new feature or problem, before `/ce-plan`
- When you don't yet have a clear understanding of the requirements
- To ground ideas in evidence before planning begins
- After `/ce-ideate` when you have candidate ideas to validate

## Prompt

```
<question-blocking>
You are now in brainstorm mode. I will ask you one question at a time. Answer each question thoroughly before I ask the next.

Your goal: Produce a requirements-only unified plan that describes WHAT needs to be built (not HOW).

Please answer the following question (or type 'done' if you have no more information to add):

{{question}}
</question-blocking>

<gap-lenses>
After each answer, we will pressure-test using these named gap lenses. If any lens reveals a gap, we will address it before moving on:

1. **Evidence** - What evidence supports this premise? Is it empirical, anecdotal, or assumed?
2. **Specificity** - Is this specific enough to be testable and implementable, or is it vague?
3. **Counterfactual** - What would need to be different for this to not be the right approach?
4. **Attachment** - Are we attached to this approach for reasons unrelated to its merit?
5. **Durability** - Will this still make sense in 6 months when context has changed?
</gap-lenses>

<origin-tracing>
R/A/F/AE IDs will be traced from this brainstorm through /ce-plan and /ce-work:
- R (Requirement): What user need or problem statement does this address?
- A (Assumption): What assumptions are we making that could change?
- F (Fact): What established facts does this rely on?
- AE (Area of Expertise): Which domain expertise informs this decision?
</origin-tracing>

<output-format>
When complete, this brainstorm will produce a unified plan document with these sections:
- Problem statement
- Goals and success metrics
- Key requirements (functional and non-functional)
- Constraints
- Open questions / risks
- Stakeholders

The output will be requirements-only - no implementation details, code, or step-by-step instructions.
</output-format>