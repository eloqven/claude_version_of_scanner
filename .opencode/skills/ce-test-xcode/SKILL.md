---
name: ce-test-xcode
description: Build and test iOS apps on the iOS simulator. Validates that iOS-specific changes work correctly.
---

# /ce-test-xcode

## Description

Build and test iOS apps on the iOS simulator. Validates that iOS-specific changes work correctly.

## When to Use

- After iOS-specific changes
- Before merging iOS-related PRs
- When testing on iOS simulator
- Before `/ce-code-review` for iOS changes

## Input

- **scheme**: Xcode scheme to build
- **device**: Simulator device (iPhone 15, iPad Air, etc.)
- **tests**: Specific tests to run

## Output

- **Build results**: Xcode build status
- **Test results**: Simulator test results
- **File**: `docs/tests/xcode-test-{timestamp}.md`

## Configuration

```yaml
ce-test-xcode:
  scheme: "auto"  # Auto-detect or specify scheme
  device: "iPhone 15"  # Default simulator device
  os-version: "17.0"  # iOS version
  timeout: "10m"  # Build/test timeout
```

## allowed-tools

- Bash: `xcodebuild`, `xcrun simctl`
- Read: Read Xcode project files

## Prompt

```
<xcode-testing>
Build and test iOS app on iOS simulator.

<test-process>
1. Identify Xcode scheme and project
2. Build the app for simulator
3. Boot iOS simulator
4. Install and run tests
5. Capture results and logs
6. Report findings
</test-process>

<test-scenarios>
Test common iOS scenarios:
- App launches successfully
- Core features work on device
- No crashes or errors
- Performance is acceptable
- UI renders correctly on different screen sizes
</test-scenarios>
</xcode-testing>

<output-format>
Xcode test results:
```
docs/tests/xcode-test-{timestamp}.md
├── scheme: <scheme name>
├── device: <device name>
├── build-status: {success|failure}
├── test-results:
│   ├── {test-name}: {pass|fail|skipped}
│   └── ...
├── logs: <relevant build/test logs>
├── summary: {passed}/{total} tests passed
└── recommendations: <next steps>
```
</output-format>

## Examples

### Basic Xcode test:
```
/ce-test-xcode
```

### Test specific scheme:
```
/ce-test-xcode --scheme "MyApp"
```

### Test on iPad:
```
/ce-test-xcode --device "iPad Air" --os-version "17.0"
```

## Notes

- Requires Xcode and iOS simulator installed
- Auto-detects scheme if not specified
- Integrates with /ce-code-review for iOS changes