---
name: ce-test-browser
description: Run browser tests on PR-affected pages. Validates that changes work correctly in a browser environment.
---

# /ce-test-browser

## Description

Run browser tests on PR-affected pages. Validates that changes work correctly in a browser environment.

## When to Use

- After frontend changes
- Before merging UI-related PRs
- When browser compatibility is a concern
- Before `/ce-code-review` for frontend changes

## Input

- **pages**: Pages to test (defaults to changed files)
- **browsers**: Browsers to test on (chrome, firefox, safari)
- **scenarios**: User scenarios to test

## Output

- **Test results**: Browser test results
- **File**: `docs/tests/browser-test-{timestamp}.md`

## Configuration

```yaml
ce-test-browser:
  browsers: ["chrome"]  # chrome | firefox | safari | edge
  headless: true  # Run in headless mode
  viewport: "1920x1080"  # Default viewport size
  timeout: "30s"  # Test timeout
```

## allowed-tools

- Bash: Run browser tests, start dev server
- Read: Read test files
- Glob: Find test files

## Prompt

```
<browser-testing>
Run browser tests on PR-affected pages.

<test-process>
1. Identify changed files and affected pages
2. Start dev server if needed
3. Run browser tests on specified browsers
4. Capture screenshots of failures
5. Report results with evidence
</test-process>

<test-scenarios>
Test common user scenarios:
- Page loads correctly
- Interactive elements work
- Forms submit properly
- Navigation works
- Responsive design on different viewports
</test-scenarios>
</browser-testing>

<output-format>
Browser test results:
```
docs/tests/browser-test-{timestamp}.md
├── pages-tested: <list of pages>
├── browsers: <list of browsers>
├── results:
│   ├── {page}:
│   │   ├── {browser}: {pass|fail}
│   │   ├── screenshots: <if any>
│   │   └── notes: <any issues>
│   └── ...
├── summary: {passed}/{total} tests passed
└── recommendations: <next steps>
```
</output-format>

## Examples

### Basic browser test:
```
/ce-test-browser
```

### Test specific pages:
```
/ce-test-browser --pages "/checkout,/profile"
```

### Test on multiple browsers:
```
/ce-test-browser --browsers chrome,firefox,safari
```

## Notes

- Automatically detects changed files and affected pages
- Runs in headless mode by default
- Integrates with /ce-code-review for frontend changes