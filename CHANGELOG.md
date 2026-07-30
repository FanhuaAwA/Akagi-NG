# Changelog

All notable changes to Akagi-NG are documented in this file.

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
