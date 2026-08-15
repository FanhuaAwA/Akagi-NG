# Changelog

All notable changes to Akagi-NG are documented in this file.

## [1.2.6] - 2026-08-15

### Fixed

- Fixed FlyA stateful sessions accumulating events across consecutive hands when
  auto-playing continues into the next hand. The observed event stream is now
  rebuilt from a fresh `start_game` boundary after each `end_kyoku`, matching
  the reconnect recovery path and preventing `state_replay_invalid` rejections
  on long multi-hand sessions.
- Upgraded the FlyA decision request schema to `flya-mahjong-events-v2` and
  stopped sending the optional `state_digest`, leaving digest validation to the
  server and removing a potential `state_digest_mismatch` source.
- Fixed Auto Join stalling after the result confirmations when the Mahjong Soul
  lobby color anchors drift after a client UI update. The lobby-readiness check
  now falls back to a stable-window coordinate gate after a bounded anchor grace
  window, so the ranked-lobby/room/mode click chain still runs instead of
  aborting. Anchor sampling also logs the measured colors for recalibration.
- Recalibrated the Auto Join ranked-lobby, room and mode button coordinates for
  the current Mahjong Soul client layout, including the post-scroll positions of
  the jade and throne rooms.
- Fixed blank blue toasts emitted when Mahjong Soul reports lobby readiness or
  matchmaking start: the `lobby_ready` and `matching_started` notification codes
  now have UI config and translations in all four locales.

### Testing

- Added a regression that drives a normal hand boundary (`end_kyoku` without a
  pending action) followed by the next `start_kyoku`, asserting the FlyA event
  stream and session are rebuilt exactly like the reconnect path.
- Added an Auto Join regression asserting the lobby-readiness coordinate
  fallback passes once the color-anchor grace window is crossed.

## [1.2.5] - 2026-08-15

### Added

- Added Creem as a second OT3/FlyA purchase channel alongside PayPal, with a
  checkout creation endpoint, result polling, and a new settings dialog flow.
- Added an authenticated `/api/health` readiness endpoint that exposes the
  running service version and PID to the desktop shell.

### Fixed

- Made DataServer startup transactional: the main backend now waits for a real
  socket bind, exits immediately on startup failure, and preserves the native
  `errno`/Windows error details for every rejected candidate port.
- Replaced the generic Electron backend timeout with an authenticated
  `/api/health` readiness check, PID validation, bounded stderr diagnostics,
  and direct propagation of the concrete startup error to the dashboard.
- Delayed optional Mihomo startup until the backend health check succeeds and
  updated API/SSE connections when DataServer falls back to a different port.
- Normalized `kita` events to `nukidora` in the action planner so the
  three-player North draw is matched by both MJAI consumers.
- Rendered the dashboard against the resolved backend endpoint instead of the
  preloaded placeholder, keeping API/SSE traffic on the actually bound port.

### Changed

- OT3 health now reports the aggregate queue depth and worker liveness instead
  of the per-model map, with backwards compatibility for older servers.
- Extended the desktop backend readiness timeout to 60 seconds to accommodate
  first-run torch imports on slower machines.

### Testing

- Added regressions for an exhausted port range, Windows-style bind permission
  failures, fallback-port persistence, backend exit/error propagation, health
  identity validation, backend-before-Mihomo startup ordering, and Creem
  checkout parsing.

## [1.2.4] - 2026-08-11

### Added

- Re-enabled Mahjong Soul automatic joining with protocol and visual lobby
  guards, stable-window checks, targeted result-button detection,
  protocol-confirmed matchmaking, and an optional completed-game limit.
- Added privacy-filtered FlyA replay diagnostics under the application log
  directory to make `state_replay_invalid` failures reproducible without
  storing API credentials or HTTP headers.

### Changed

- Enabled libriichi quick evaluation for forced legal actions so mandatory
  discards no longer spend an unnecessary local or online inference request;
  Riichi Lookahead now represents a forced discard as a 100% candidate.
- Kept the Mahjong Soul UI-readiness floor for three-player North extraction
  and expanded window-title matching for localized client titles.
- Documented the independent plugin release lifecycle. Riichi City Local
  Unlock now uses Akagi-NG's bundled `ruamel.yaml` and does not require a
  user-installed PyYAML package.

### Fixed

- Fixed stateful FlyA sessions being reused after the recommended local action
  was not executed or was replaced by a different UI/manual action. The client
  now reconciles discard, North extraction and two-stage riichi events, rotates
  uncertain or divergent sessions, and stops remote replay when an expected
  action is missing from the observed stream.
- Fixed Mahjong Soul dealer starting hands sorting all 14 tiles and therefore
  losing the real drawn-tile identity, which could corrupt `tsumogiri`
  semantics and lead to FlyA `state_replay_invalid` responses.
- Fixed direct-IP Mahjong Soul `/gateway` WebSocket connections falling back to
  a stale platform bridge selection.
- Fixed proxy loops when Clash Verge is configured as the upstream proxy for
  Akagi-NG's mihomo TUN by routing `verge-mihomo.exe` directly before game
  domain interception rules.

### Testing

- Added regressions for FlyA action reconciliation and diagnostic artifacts,
  dealer opening-hand identity, auto-join lifecycle and limits, forced-action
  quick evaluation, Mahjong Soul direct-IP routing, and mihomo proxy bypasses.
- Expanded GitHub Actions quality gates with logger resilience, mihomo
  configuration, privilege-boundary, and packaged Electron trust-boundary
  checks.

## [1.2.3] - 2026-08-10

### Added

- Added an independent plugin repository with remote catalog installation,
  SHA-256 verified downloads, atomic updates, and local ZIP imports.
- Added package validation for duplicate plugin IDs, manifest/runtime metadata,
  archive traversal, symlinks, encrypted entries, and expansion limits.
- Added Riichi City Local Unlock as an external plugin alongside Mahjong Soul
  MAX, including HTTP response and WebSocket lifecycle hooks.

### Changed

- Moved Mahjong Soul MAX out of the Akagi-NG release and into the independent
  plugin repository so cosmetic unlock updates no longer require a desktop
  application release.
- Enforced symmetric plugin conflicts in the backend. Mahjong Soul MAX and
  Riichi City Local Unlock cannot be enabled at the same time, including when
  restoring manually edited persisted state.

### Fixed

- Removed stale setuptools build trees before packaging so files deleted from
  the source tree cannot leak back into release archives from an older build.
- Fixed recognized Riichi City WebSocket endpoints being forced through the
  previously selected Tenhou bridge, which caused binary frames to be decoded
  as UTF-32 and reported as game parsing failures.

## [1.2.2] - 2026-08-08

### Fixed

- Fixed Tenhou wins being emitted as `end_kyoku` without the required `hora`
  event. FlyAPI can now replay state across round boundaries, including
  multi-ron hands and game-ending wins.
- Fixed Tenhou discards immediately after `chi`/`pon` being marked as
  `tsumogiri`, even though the caller did not draw a tile. Draw state is now
  tracked per actor so FlyAPI can replay called hands legally.
- Fixed Tenhou pon decoding applying the called-tile index before the unused
  physical-copy swap. This could remove the wrong tile from the local hand,
  especially around red fives, and corrupt later replay state.
- Fixed unrelated or duplicate Chromium WebSocket lifecycle events being
  counted as Tenhou/Mahjong Soul disconnects by tracking CDP `requestId`
  values end to end. Tenhou relay/IP endpoints are adopted only after a frame
  is successfully parsed as a game event.
- Fixed permanent FlyAPI replay errors being retried with the same invalid
  history under new session IDs. Transport disconnects now invalidate the
  observed replay immediately; Akagi uses local fallback for the incomplete
  hand and resumes FlyAPI from the next complete `start_kyoku` baseline.
- Fixed self-riichi draws unnecessarily invoking FlyA, OT3, or the local model.
  Once riichi is accepted, Akagi advances protocol state without inference and
  directly returns the mandatory tsumogiri action.
- Fixed settings saves failing when switching from external proxy mode to the
  built-in game window while mihomo TUN was enabled. The TUN runtime now stops
  in built-in-window mode while retaining the user's preference, then starts
  automatically when external proxy mode is restored.

### Changed

- Removed the isolated elevated TUN helper and its UAC/named-pipe startup path.
  The Windows desktop now requests administrator rights at process startup and
  launches the bundled mihomo process directly, so helper connection failures
  can no longer be misreported as UAC approval timeouts.

## [1.2.1] - 2026-08-07

### Added

- Added a dedicated automation page that separates automatic play from the
  general settings page and provides per-action minimum/maximum delay sliders
  for discards, riichi, calls, kans, wins, abortive draws, kita, skip, and
  candidate selection.
- Added built-in humanized timing ranges and real-time timing updates without
  routine save notifications. Changing or enabling the timing model still
  produces an explicit notification.
- Added HTTP interception records to the Log page, including sanitized request
  and response metadata for inspecting Mahjong Soul telemetry.
- Added sanitized FlyA HTTP diagnostics for decision, model-list, quota, and key
  activation requests. Logs include endpoint, attempt, status, latency, safe
  error code, request/session IDs, sequence bounds, and state digest without API
  keys or authorization headers.
- Added delayed hover labels for the automation, settings, plugins, and logs
  navigation buttons.

### Fixed

- Rewrote Firefox-family MITM certificate metadata so Akagi's CA identity is no
  longer exposed directly in Mahjong Soul telemetry. Chromium users remain
  outside the affected certificate-reporting path.
- Fixed mandatory actions becoming `none` when FlyA returns
  `decision_not_required`, `state_replay_incomplete`, or
  `state_replay_invalid`. Akagi now immediately replays the hand through local
  Mortal, uses that decision, rotates the FlyA session, and sends the complete
  observed event stream on the next request.
- Fixed FlyA suppression logs losing the server error code, which previously
  made state synchronization failures look like generic connectivity failures.
- Hardened automatic-play input against early, late, stale, and incorrect
  clicks by validating the live Mahjong Soul operation step before and after
  cursor movement, checking target-window ownership, waiting for UI readiness,
  and verifying that actionable operation buttons clear after a click.
- Fixed riichi automation so selecting riichi is followed by the required tile
  discard, and improved handling for pon, chi, kan, kita, win, draw, and skip
  operation buttons.
- Notifications can now be dismissed immediately by clicking them.
- Added a packaged default-settings fallback so older or incomplete settings no
  longer cause `Cannot read properties of undefined (reading 'enabled')`.
- Fixed an Electron main-process crash caused by `EPIPE` when a launcher or
  terminal closed its inherited stdout/stderr pipe while mihomo was still
  emitting logs. Console output now fails closed while file logging continues.

### Changed

- Automatic join is temporarily hard-disabled in both backend and frontend.
  Stale configurations containing `enabled=true` are ignored and cannot start a
  background click task while reliable lobby/result-screen recognition is being
  completed.
- Timing-only automatic-play changes apply immediately and silently; functional
  mode changes retain user-visible feedback.

### Verification

- Added regression coverage for FlyA suppression fallback/session rebuilding,
  sanitized HTTP diagnostics, automatic-join hard disablement, operation-step
  validation, per-action timing, settings migration, certificate rewriting, and
  HTTP capture.

## [1.2.0] - 2026-08-05

### Added

- Added real-time inference lifecycle status for the legacy AkagiOT engine,
  OT3, and FlyA. Requesting, success, and error states now flow through SSE to
  both the Dashboard header and HUD, with provider, model, request ID, and
  elapsed-time context.
- Added a local plugin framework, Dashboard extension panel, persisted plugin
  state, and the built-in MajsoulMax skin-unlock adapter.

### Fixed

- Fixed MajsoulMax and AI being mutually exclusive by running both in one
  mitmproxy connection with a deterministic processing order:
  `game -> MajsoulMax plugin -> platform bridge / AI -> server`.
- Fixed rewritten protobuf responses that omit an empty method-name field from
  disappearing before AI processing. Response parsing now follows protobuf
  field IDs instead of positional assumptions.
- Manual Liqi protocol updates now build and validate the complete descriptor
  pool before atomically replacing the user override, so malformed downloads
  cannot corrupt a previously working protocol file.
- Plugin hot toggles no longer restart MITM while the aggregate MITM requirement
  remains enabled, avoiding unnecessary active WebSocket disconnects. MITM
  host, port, or upstream changes still force a controlled restart.
- Plugin-only MITM demand is now shared with the built-in mihomo lifecycle.
  Effective proxy-route transitions close an already-open embedded game window
  with an explicit restart notice instead of leaving it on a stale route.
- Added a MITM ready/failure handshake so bind errors are reported instead of a
  starting proxy being exposed as ready. Game sessions also reset stale proxy
  state when MITM is disabled and normalize wildcard listen addresses to
  loopback for client use.

### Performance

- Removed the packaged full-tree resource manifest, signature, inventory, and
  SHA-256 startup pass. Startup now performs only bounded availability checks
  for two native libraries and the model directory before starting the backend.
- The Dashboard no longer waits for Python startup or the settings API before
  rendering. It loads from a sanitized local startup snapshot, keeps
  backend-dependent controls disabled until ready, and reports startup progress
  or failure inside the main interface.
- Removed fixed startup and exit waits, tightened backend readiness polling, and
  made HUD creation lazy so normal Dashboard startup does less work.
- Unified desktop shutdown into one idempotent path and shortened graceful SSE
  teardown so repeated quit signals do not duplicate backend or mihomo cleanup.
- Reduced the backend and elevated mihomo forced-shutdown ceilings from five to
  two seconds while retaining graceful shutdown first.
- Startup logs record Electron readiness, Dashboard load, resource availability,
  backend spawn, and backend-ready timings for future diagnosis.

### Security

- Changed the packaged Windows desktop executable from `requireAdministrator`
  to `asInvoker`, so the Electron UI and Python backend no longer run with
  administrator privileges.
- Added a fixed-function elevated TUN helper. It validates a pinned mihomo
  SHA-256, copies the verified binary and validated configuration into an
  administrator-only ProgramData directory, and contains mihomo in a
  kill-on-close Windows Job Object.
- Restricted TUN lifecycle IPC to the trusted dashboard main frame, removed the
  unguarded direct-start IPC route, and serialized start/reconcile/stop work.
- Excluded mihomo from build-time Authenticode rewriting so its pinned hash
  remains reproducible. This release does not include trusted Authenticode or
  Apple Developer ID/notarization credentials, so artifacts do not claim an
  operating-system publisher identity.
- Replaced the renderer's arbitrary-channel IPC bridge with named capabilities,
  enforced dashboard/HUD role and main-frame authorization on every handler,
  blocked untrusted navigation, child frames, permissions, and popups, restricted
  game navigation to HTTPS platform allowlists, and added a restrictive renderer CSP.

### Changed

- Normal startup now renders the dashboard before optional TUN elevation. UAC is
  requested only when bundled TUN is enabled, and rejection does not prevent the
  unprivileged desktop or backend from running.
- Shutdown now stops the elevated TUN lifecycle before the local backend.
- Moved live OT3 and FlyA HTTP inference onto a bounded daemon worker with
  absolute end-to-end deadlines, generation cancellation, and fail-fast local
  fallback when capacity is exhausted. API shutdown now signals the application
  stop event directly after queuing the lifecycle message.
- Moved mutable settings, secrets, logs, plugin state/configuration, and
  downloaded Liqi overrides into the operating-system user-data directory.
  Existing portable-root configuration is migrated on first run, so Linux
  AppImage and macOS bundles no longer write into immutable package roots.
- Release publication is now atomic: all three platform packages are built,
  verified, and staged before one job creates the public GitHub Release.

### Verification

- Added automated privilege-boundary coverage for PE manifests, helper protocol
  parsing, strict TUN configuration validation, mihomo hash pinning, IPC trust
  checks, startup/shutdown ordering, and packaged artifact contents.
- Added fast resource-availability regressions for required native libraries and
  optional local model files.
- Added Electron trust-boundary regressions for renderer URLs, IPC role policy,
  external/game URL allowlists, preload exposure, navigation guards, and CSP.
- Added online-inference regressions for worker isolation, bounded capacity,
  deadlines, stale-generation rejection, shutdown cancellation, and 30-run
  network-blackhole exit latency.
- Added inference-status lifecycle and SSE replay coverage across legacy, OT3,
  and FlyA execution paths.
- Added plugin persistence, hook isolation, protobuf response compatibility,
  plugin-to-AI ordering, MITM startup/failure, and hot-toggle lifecycle coverage.
- Added Electron startup, lazy-HUD, direct-proxy reset, and single-path shutdown
  regression checks.

## [1.1.2] - 2026-08-02

### Added

- Added FlyA Test as an online API provider with server-authoritative probability
  decisions and native Akagi-NG fallback behavior.
- Added FlyA model selection, quota lookup, credential verification, and service
  management to the settings interface.
- Added secure cross-platform credential storage for online provider secrets.

### Changed

- Restored the standard Electron HUD as the supported overlay and kept mouse
  click-through independent from capture protection.
- Kept the dashboard visible and recoverable while applying best-effort capture
  protection, and removed the experimental advanced/Discord overlay path.

### Verification

- Expanded backend coverage for FlyA decisions, service management, settings,
  credential storage, tracker state, and API routes.
- Added frontend regression coverage for desktop window and HUD interaction policies.

## [1.1.0] - 2026-07-31

### Added

- Added an optional tray icon without hiding the dashboard or its taskbar entry.
- Added an independent mouse click-through toggle to the standard HUD. The
  control island remains interactive so click-through can always be disabled
  and the HUD can be closed.

### Changed

- The standard Electron HUD is now the only overlay mode. It remains draggable,
  resizable, excluded from the taskbar, and uses the existing Mahjong display.
- Standard HUD windows now enable Electron content protection when configured.
- Capture protection now also applies to the visible Dashboard without hiding
  it or removing its taskbar entry.
- Removed the experimental dashboard Privacy Mode, start-hidden behavior, and
  recovery shortcut after they could leave the main interface inaccessible.
- Legacy `privacy_mode` and `start_hidden` values are now ignored and normalized
  to disabled when settings are loaded or saved.
- When the tray icon is disabled, closing the dashboard now minimizes it to the
  taskbar instead of hiding it in the background.
- Desktop settings can be reconciled without restarting the Python backend.
- Removed the experimental native/Discord advanced overlay implementation,
  settings, CI job, and packaged binary.
- Update checks and packaged publishing metadata now target
  `FanhuaAwA/Akagi-NG`.

### Security and limitations

- Capture protection is a best-effort Windows feature and is not DRM. It cannot
  guarantee exclusion from every OBS capture method or external camera.
- Capture protection and HUD click-through are independent settings.

## [1.0.9] - 2026-07-31

### Added

- Added OT3 online-model support, including authenticated inference and selectable
  four-player/three-player models returned by the service.
- Added OT3 key query, service health check, model-list refresh, redemption-code
  exchange, and purchase entry points to the settings panel.
- Added optional per-provider proxy configuration for OT3 network requests.
- Bundled mihomo v1.19.29 for Windows x64 as an optional game-traffic TUN core,
  replacing the previous Proxifier-based workflow.
- Added automatic mihomo configuration generation, lifecycle management, port
  validation, game routing rules, and live reconciliation after settings changes.

### Fixed

- Fixed OT3 management requests returning HTTP 403 by matching the Akagi-3 server
  contract and using the production HTTPS service endpoint.
- Fixed OT3 response and event conversion so online decisions can flow through the
  existing Akagi-NG tracker and UI.
- Fixed external MITM proxy settings not being applied until a full application
  restart by reconciling the backend and mihomo processes when settings are saved.
- Added direct-process and loopback bypass rules to prevent proxy recursion.
- Added a Clash/Clash Verge coexistence path:
  `game -> Akagi mihomo -> Akagi MITM -> Clash -> network`.
- Improved state tracking for reconnect/resume and delayed game-start event
  sequences.

### Security

- OT3 API credentials are sent with the expected authorization mechanism and are
  not written to application logs.
- Local configuration and credentials are excluded from packaged release assets.

### Usage notes

- The bundled mihomo TUN mode requires administrator privileges on Windows.
- Trust the local mitmproxy CA certificate before using external MITM mode.
- When Akagi-NG owns the TUN interface, disable Clash TUN and configure Clash's
  local HTTP/mixed port as Akagi-NG's upstream proxy. Keep all Akagi-NG, Clash,
  backend, MITM, mixed, and controller ports unique.

[1.0.9]: https://github.com/FanhuaAwA/Akagi-NG/releases/tag/v1.0.9
[1.1.0]: https://github.com/FanhuaAwA/Akagi-NG/releases/tag/v1.1.0
[1.1.2]: https://github.com/FanhuaAwA/Akagi-NG/releases/tag/v1.1.2
[1.2.0]: https://github.com/FanhuaAwA/Akagi-NG/compare/v1.1.2...v1.2.0
[1.2.1]: https://github.com/FanhuaAwA/Akagi-NG/compare/v1.2.0...v1.2.1
[1.2.2]: https://github.com/FanhuaAwA/Akagi-NG/compare/v1.2.1...v1.2.2
[1.2.3]: https://github.com/FanhuaAwA/Akagi-NG/compare/v1.2.2...v1.2.3
[1.2.4]: https://github.com/FanhuaAwA/Akagi-NG/compare/v1.2.3...v1.2.4
[Unreleased]: https://github.com/FanhuaAwA/Akagi-NG/compare/v1.2.4...HEAD
