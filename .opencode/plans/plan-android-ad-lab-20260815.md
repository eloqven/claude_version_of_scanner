# Android Ad Lab — Implementation Plan

**Author:** Nexus  
**Date:** 2026-08-15  
**Source:** Android Ad Lab — Initial Implementation.md  
**Status:** Planning Phase (Read-Only)

---

## 1. Summary

Build a minimal Android application ("Android Ad Lab") establishing the foundational playback component using Kotlin, Jetpack Compose, Media3/ExoPlayer, and Coroutines/Flow. The app plays a bundled test video with basic controls (play/pause, 1×/2×/4× speed) and exposes player state through a dedicated controller layer. No ad SDKs, external networks, or complex infrastructure.

---

## 2. Requirements

### REQ-001: Project Setup & Build Configuration
- **Acceptance Criteria:** Given a fresh clone, when running ./gradlew assembleDebug, then the project builds successfully with no errors
- **Priority:** Must
- **Dependencies:** []
- **Risk:** P1 (Gradle/Media3 version compatibility)
- **Autofix Class:** BuildConfig

### REQ-002: Bundled Test Video Asset
- **Acceptance Criteria:** Given the app APK, when installed on device/emulator, then a local test video file is accessible via aw or ssets resource
- **Priority:** Must
- **Dependencies:** [REQ-001]
- **Risk:** P2 (File size, format compatibility)
- **Autofix Class:** AssetManagement

### REQ-003: Player Controller Layer
- **Acceptance Criteria:** Given the app running, when the controller is instantiated, then it exposes: playback state (StateFlow), current position (StateFlow), duration (StateFlow), playback speed (StateFlow), playing/paused (StateFlow); and supports play(), pause(), setSpeed(1.0/2.0/4.0)
- **Priority:** Must
- **Dependencies:** [REQ-001]
- **Risk:** P1 (Lifecycle management, memory leaks)
- **Autofix Class:** StateManagement

### REQ-004: Android Lifecycle Integration
- **Acceptance Criteria:** Given activity lifecycle events (onStart/onStop/onDestroy), when they occur, then player is created on start, released on destroy, and playback pauses/stops appropriately on stop
- **Priority:** Must
- **Dependencies:** [REQ-003]
- **Risk:** P0 (Crashes, resource leaks if wrong)
- **Autofix Class:** Lifecycle

### REQ-005: Main Screen UI — "Android Ad Lab"
- **Acceptance Criteria:** Given the app launches, when the main activity loads, then a screen titled "Android Ad Lab" displays with video surface and controls
- **Priority:** Must
- **Dependencies:** [REQ-001, REQ-003]
- **Risk:** P2 (Compose rendering)
- **Autofix Class:** UI

### REQ-006: Playback Controls UI
- **Acceptance Criteria:** Given video is loaded, when user taps Play/Pause button, then playback toggles; when user selects 1×/2×/4×, then playback speed changes immediately and UI reflects current speed
- **Priority:** Must
- **Dependencies:** [REQ-003, REQ-005]
- **Risk:** P1 (State synchronization)
- **Autofix Class:** UI

### REQ-007: Continuous Position/Duration Updates
- **Acceptance Criteria:** Given video is playing, when position changes, then UI updates position and duration in real-time (≤100ms latency)
- **Priority:** Must
- **Dependencies:** [REQ-003, REQ-006]
- **Risk:** P2 (Performance, jank)
- **Autofix Class:** StateManagement

### REQ-008: README Documentation
- **Acceptance Criteria:** Given the repo, when reading README.md, then it describes project structure, how to build, how to run, and current capabilities
- **Priority:** Should
- **Dependencies:** [REQ-001]
- **Risk:** P3 (Low)
- **Autofix Class:** Documentation

---

## 3. Test Scenarios

| Scenario | Given | When | Then |
|----------|-------|------|------|
| TS-001: Build Success | Clean repo | ./gradlew assembleDebug | BUILD SUCCESSFUL |
| TS-002: App Launch | Built APK installed | App icon tapped | Main screen "Android Ad Lab" visible |
| TS-003: Video Auto-load | Main screen visible | — | Video surface shows first frame |
| TS-004: Play/Pause Toggle | Video loaded | Tap Play | Video plays; tap Pause → video pauses |
| TS-005: Speed 1× | Video playing | Select 1× | Playback at normal speed |
| TS-006: Speed 2× | Video playing | Select 2× | Playback at 2× speed |
| TS-007: Speed 4× | Video playing | Select 4× | Playback at 4× speed |
| TS-008: Position Updates | Video playing | Wait 5s | Position label updates continuously |
| TS-009: Duration Available | Video loaded | — | Duration label shows total length |
| TS-010: Lifecycle Pause | Video playing | Home button / background | Playback pauses, resources held |
| TS-011: Lifecycle Resume | App backgrounded | Return to app | Playback resumes from position |
| TS-012: Lifecycle Destroy | App running | Swipe away / kill | Player released, no leaks |
| TS-013: Orientation Change | Video playing | Rotate device | Playback continues, state preserved |

---

## 4. Risks & Mitigations

| ID | Risk | Severity | Mitigation |
|----|------|----------|------------|
| R-001 | Media3/ExoPlayer version conflicts with Compose/Kotlin | P1 | Pin compatible versions in libs.versions.toml; test matrix |
| R-002 | Player not released → memory leak / crash on recreate | P0 | Use DisposableEffect / ememberPlayer pattern; verify in LeakCanary |
| R-003 | Video format not supported on all API levels | P2 | Use H.264 baseline + AAC in MP4; test API 24+ |
| R-004 | StateFlow updates on background thread → Compose crash | P1 | Ensure stateIn(viewModelScope, SharingStarted.WhileSubscribed(), initialValue) |
| R-005 | Large test video bloats APK | P2 | Keep video <10MB; use aw resource (uncompressed) or ssets |
| R-006 | Speed change causes audio pitch shift / stutter | P2 | ExoPlayer handles this; verify on device |

---

## 5. Out of Scope (Explicit)

- Real ad SDKs (IMA, Google Ads, etc.)
- Ad network integration
- AccessibilityService
- VPN / proxy functionality
- Root detection / hooking frameworks
- Backend services / APIs
- Databases (Room, Realm, etc.)
- Authentication / user accounts
- Analytics platforms (Firebase, Amplitude, etc.)
- Complex DI (Hilt, Koin, Dagger)
- Plugin / module systems
- Multi-module architecture
- Unit/UI test suites (beyond manual verification)
- CI/CD pipeline
- Release signing / Play Store prep

---

## 6. Definition of Done (Plan → Work Gate)

- [ ] All 8 requirements have U-IDs, acceptance criteria, priorities, dependencies, risks
- [ ] Test scenarios cover all Must-priority requirements
- [ ] Risks identified with P0-P3 severity and mitigations
- [ ] Out-of-scope list matches source document exactly
- [ ] Plan saved to D:/agents/Nexus/plan-android-ad-lab-20260815.md
- [ ] User confirms plan readiness

---

## 7. Confidence Check

**Confidence: 4/5**

Strong alignment with source document. Only uncertainty: exact Media3/ExoPlayer/Kotlin/Compose version matrix — will resolve during REQ-001 implementation by checking official compatibility guides.

---

**— Nexus"
