# Changelog

All notable changes to Raptor Link can be documented here.

The format is based on a simple version history intended for GitHub Releases.

## 0.3.7 Beta

- Added a real OpenRGB SDK source using the local OpenRGB server on port 6742.
- Added selectable Beta source presets for MSI Mystic Light, Gigabyte RGB Fusion, ASUS Aura / Armoury Crate, Razer Chroma, Logitech G HUB, SteelSeries GG / Prism and generic OpenRGB hardware.
- Corsair iCUE is explicitly marked **Tested & Validated**.
- All other RGB ecosystems are explicitly marked **Beta / In development** in the source selector and compatibility pages.
- OpenRGB devices and LEDs are detected and can be mapped to WLED zones.
- OpenRGB failures are isolated from the iCUE/WLED engine and reconnect automatically.
- Added OpenRGB protocol and worker regression tests.

> Beta brand integrations currently use OpenRGB as the hardware bridge. They do not yet hook directly into each vendor application’s private animation engine.

## 0.3.6 Beta

- Startup check for newer GitHub Releases, without blocking startup when offline.
- Built-in Help & Feedback center for questions, bugs, suggestions, comments and RGB compatibility reports.
- Optional one-time 1–5 star prompt after roughly one hour of cumulative use.
- Community hardware-testing cards for MSI Mystic Light, Gigabyte RGB Fusion, ASUS Aura / Armoury Crate, Razer Chroma, Logitech G HUB, SteelSeries GG / Prism and OpenRGB.
- Optional non-sensitive diagnostics can be attached to a GitHub report.
- Includes the isolated iCUE watchdog introduced for the 0.3.5 stability fix.

> The additional RGB ecosystems are testing targets, not advertised as supported connectors yet.

## Unreleased

### Planned / in development

- WLED multi-device management
- Corsair iCUE integration improvements
- visual screen-to-LED mapping
- multi-monitor mapping
- WLED preset support
- direct access to WLED web interfaces
- segment-based LED configuration

## Releases

Release notes will be added here when public installers are published.
