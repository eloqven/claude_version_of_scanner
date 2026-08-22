---
name: ce-prototype
description: Build a throwaway prototype so someone can experience how the product should work, feel, or read. Focus on speed and experience over correctness.
---

# /ce-prototype

## Description

Build a throwaway prototype so someone can experience how the product should work, feel, or read. Focus on speed and experience over correctness.

## When to Use

- When you need to validate UX before full implementation
- When exploring a new interaction pattern
- When you need to "show" rather than "tell"
- Before `/ce-brainstorm` to validate ideas with users

## Input

- **feature**: The feature or interaction to prototype
- **constraints**: Technical constraints, timeline, resources
- **target**: Who will experience the prototype

## Output

- **File**: `docs/prototypes/prototype-{feature}-{timestamp}.md`
- **Code**: Throwaway prototype code (not production quality)

## Configuration

```yaml
ce-prototype:
  fidelity: "medium"  # low | medium | high
  duration: "2h"  # Time budget for prototype
  target-platform: "web"  # web | mobile | desktop | cli
  include-user-test: false  # Whether to include user testing
```

## allowed-tools

- Read: Read existing code and patterns
- Write: Create prototype files
- Bash: Run prototype, install dependencies
- Glob: Find relevant project files

## Prompt

```
<prototype-creation>
Build a throwaway prototype for: {{feature}}

<prototype-principles>
- Speed over correctness: Get something working fast
- Experience over implementation: Focus on how it feels
- Minimal code: Just enough to demonstrate the concept
- Clear boundaries: Document what's real vs. mocked
</prototype-principles>

<prototype-process>
1. Identify the core interaction to demonstrate
2. Build the minimum viable prototype
3. Document assumptions and shortcuts taken
4. If user-test is enabled, create a testing script
5. Document learnings and whether to proceed
</prototype-process>
</prototype-creation>

<output-format>
Prototype package:
```
docs/prototypes/prototype-{feature}-{timestamp}/
├── README.md  # What this prototype demonstrates
├── prototype.{ext}  # The prototype code
├── assumptions.md  # What's real vs. mocked
├── learnings.md  # Key insights from building/testing
└── next-steps.md  # Whether to proceed with full implementation
```
</output-format>

## Examples

### Basic prototype:
```
/ce-prototype "new dashboard layout with drag-and-drop widgets"
```

### High-fidelity web prototype:
```
/ce-prototype --fidelity high --target-platform web "mobile checkout flow"
```

### Prototype with user testing:
```
/ce-prototype --include-user-test --duration 4h "onboarding wizard"
```

## Notes

- Prototypes are throwaway - not production code
- Focus on the experience, not the implementation
- Document learnings for future `/ce-brainstorm` sessions
- Prototypes live in docs/prototypes/ and are cleaned up after evaluation