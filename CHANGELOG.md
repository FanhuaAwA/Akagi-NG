# Changelog

All notable changes to Akagi-NG are documented in this file.

## [1.1.0] - 2026-07-31

### Added

- Added a selectable Advanced Overlay mode for Windows, implemented as a native
  Direct3D 11 and Dear ImGui renderer.
- Added optional Discord Overlay hosting based on the external HWND and swap-chain
  lifecycle demonstrated by `FanhuaAwA/discord-overlay-example`.
- Added a protected native-window host that applies
  `WDA_EXCLUDEFROMCAPTURE` when capture protection is enabled.
- Added an independent SSE client for the native overlay with reconnect handling,
  cached recommendation delivery, top-three rendering, and stale-data expiry.
- Added Privacy Mode, optional tray removal, start-hidden behavior, taskbar
  suppression, and a configurable global dashboard recovery shortcut.
- Added automatic fallback from an unavailable advanced renderer to the existing
  Electron HUD.

### Changed

- Standard Electron HUD windows now enable Electron content protection when
  configured and are excluded from the taskbar.
- Desktop settings can be reconciled without restarting the Python backend.
- Windows packages now include `AkagiAdvancedOverlay.exe`.
- Update checks and packaged publishing metadata now target
  `FanhuaAwA/Akagi-NG`.

### Security and limitations

- Privacy Mode hides application UI surfaces but does not hide Akagi-NG processes
  from Task Manager, operating-system process enumeration, or security software.
- Capture protection is a best-effort Windows feature and is not DRM. It cannot
  guarantee exclusion from every OBS capture method or external camera.
- A Discord-owned window cannot receive Akagi's display-affinity setting. When
  capture protection is enabled, Auto host mode therefore selects Akagi's own
  protected native window.

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
