---
name: ce-polish
description: Start a dev server and iterate on UX polish. Focuses on visual refinements, interaction smoothness, and user experience improvements.
---

# /ce-polish

## Description

Start a dev server and iterate on UX polish. Focuses on visual refinements, interaction smoothness, and user experience improvements.

## When to Use

- When UI/UX needs refinement
- Before shipping a feature
- When visual polish is needed
- For iterative UX improvements

## Input

- **target**: The UI component or page to polish
- **iterations**: Number of polish iterations (default: 3)
- **focus**: What to focus on (visual, interaction, performance)

## Output

- **Code**: Polished UI components
- **File**: `docs/polish/polish-{timestamp}.md`

## Configuration

```yaml
ce-polish:
  dev-server-port: 3000  # Port for dev server
  iterations: 3  # Number of polish iterations
  focus: "all"  # visual | interaction | performance | all
  auto-reload: true  # Auto-reload on changes
```

## allowed-tools

- Bash: Start dev server, run build
- Read: Read component files
- Write: Modify component files
- Glob: Find component files

## Prompt

```
<ux-polish>
Start dev server and iterate on UX polish for: {{target}}

<polish-process>
1. Start dev server
2. Review current state
3. Identify polish opportunities:
   - Visual: spacing, colors, typography, alignment
   - Interaction: hover states, transitions, feedback
   - Performance: loading states, lazy loading, optimization
4. Make improvements
5. Verify changes
6. Repeat for specified iterations
</polish-process>

<polish-criteria>
Visual polish:
- Consistent spacing and alignment
- Proper color contrast and accessibility
- Typography hierarchy
- Visual feedback for interactions

Interaction polish:
- Smooth transitions and animations
- Immediate feedback on user actions
- Proper loading states
- Error handling and messaging

Performance polish:
- Optimize rendering
- Lazy load components
- Reduce bundle size
- Improve perceived performance
</polish-criteria>
</ux-polish>

<output-format>
Polish log:
```
docs/polish/polish-{timestamp}.md
├── target: <component/page>
├── iterations:
│   ├── {n}:
│   │   ├── changes: <list of changes>
│   │   ├── before: <description>
│   │   └── after: <description>
│   └── ...
├── final-state: <summary of polished result>
└── learnings: <key insights>
```
</output-format>

## Examples

### Basic polish:
```
/ce-polish "checkout page"
```

### Visual polish only:
```
/ce-polish --focus visual --iterations 5 "dashboard widgets"
```

### Performance polish:
```
/ce-polish --focus performance "product listing page"
```

## Notes

- Starts dev server automatically
- Iterates based on specified number of iterations
- Focuses on user-perceivable improvements
- Integrates with /ce-code-review for final review