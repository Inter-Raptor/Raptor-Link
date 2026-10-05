# Changelog

All notable changes to Raptor Link can be documented here.

The format is based on a simple version history intended for GitHub Releases.

## 0.5.0 Core 3 Stable Test

- Rebuilt the runtime around a deliberately small Core 3 path instead of adding more recovery logic to Core 2.
- Normal operation now focuses on iCUE -> WLED, autonomous WLED presets, a simple rainbow source and a static color source.
- Removed screen capture, audio reactions, alarms, profiles, MSI/OpenRGB, smoothing and multi-source routing from the Core 3 runtime and UI.
- Each WLED has one independent self-recovering UDP worker. A worker exception is contained and automatically restarted without taking down the engine.
- The main engine is supervised and restarts its loop after an unexpected exception while WLED workers keep their last requested state.
- Added a separate Windows watchdog process that relaunches Raptor Link after an unexpected application exit, with a crash-loop limit.
- Realtime WLED traffic is deduplicated: unchanged RGB frames are not resent at the selected FPS. A low-rate heartbeat keeps realtime mode alive.
- Core 3 caps realtime output at 20 FPS and defaults to 12 FPS; 8 FPS is available for difficult Wi-Fi networks.
- HTTP is removed from the realtime/control hot path. ON/OFF/preset changes use small redundant JSON-over-UDP commands and are reinforced only at a low rate.
- Inactivity detection returns to Windows GetLastInputInfo only, removing cursor/key heuristics that could continually reset the idle timer.
- Saving configuration no longer disables synchronization. Changes are applied live without an intentional OFF cycle for unchanged WLEDs.
- If iCUE restarts or stalls, the last valid RGB frame is frozen instead of replacing the strip with random/black data; synchronization resumes automatically when iCUE returns.
- Added a deterministic five-second transport test: red, green, blue, white, then rainbow.
- Diagnostics now include sampled RGB values actually sent to each WLED, frame counts, deduplicated-frame counts and worker restart counts.
- Old Core 2 configurations are migrated automatically to the simplified one-source-per-WLED model.

## 0.4.1 Core 2 Beta

- Fixed the first real-hardware issue found with autonomous WLED presets: some controllers could remain stuck in realtime UDP mode and stop answering the web/HTTP API.
- Before every autonomous preset, OFF or restore command, Raptor Link now sends an explicit WLED realtime-release packet even if the current Raptor Link process did not start the realtime session.
- Added a second control path using WLED's JSON API over UDP. If the HTTP API times out or returns 503, the requested preset/ON/OFF state is sent through the notifier UDP port instead of hammering the web server for minutes.
- HTTP failure no longer blocks a realtime iCUE stream from waking and resuming.
- Added an HTTP circuit breaker: after a timeout/503, that WLED is no longer hammered every few seconds. Retries back off from 15 s to 30 s and then 60 s while the other WLED workers continue normally.
- UDP fallback is now treated as fire-and-forget rather than falsely confirmed. Raptor Link keeps a pending confirmation and converges the requested OFF/preset/restore state when the controller comes back online.
- Recovery probes are lightweight and only happen after the cooldown. If the WLED already has the requested state, Raptor Link does not restart the preset unnecessarily.
- Diagnostics now expose the control path, HTTP retry countdown, pending confirmation, fallback batch count, release packet count and UDP state-command count.
- Added regression coverage for forced realtime release, HTTP-to-UDP fallback, circuit-breaker suppression and automatic HTTP recovery.
- Hardware validation is required before merge.

## 0.4.0 Core 2 Beta

- Introduced one independent output worker per WLED controller.
- Removed periodic WLED HTTP polling from active realtime streaming.
- Added autonomous WLED preset mode for effects that do not need PC-side frame generation.
- Added persistent asynchronous diagnostics with selectable levels, retention limits and a one-click copyable support report.
- Kept iCUE/screen/audio sources in realtime mode while allowing simple effects to run directly on WLED.

## 0.3.14 Beta

- Fixed slow and unstable synchronization immediately after Windows startup.
- WLED health probes are now strictly read-only: Raptor Link no longer switches a strip off while checking its HTTP API.
- Realtime UDP output no longer waits for the WLED HTTP API to answer; colors can start as soon as the controller accepts UDP.
- Failed startup probes retry quickly in the background without interrupting an already running realtime stream.
- Raptor Link now waits for iCUE/screen source data before replacing the current WLED effect with black.
- During a short source interruption, the last valid frame is kept instead of flashing black.
- Increased the WLED realtime hold timeout from 2 s to 5 s to tolerate short Windows/iCUE/network stalls without visible blinking.

## 0.3.13 Beta

- Restored the classic Raptor Link experience: the normal UI is again centered on **Corsair iCUE** and Raptor Link's own direct sources (screen, audio and local effects).
- Corsair iCUE remains the only third-party RGB integration presented as **Tested & Validated** in normal mode.
- MSI Mystic Light, OpenRGB and the other vendor experiments are hidden behind **Settings → Advanced → RGB lab** and are disabled by default.
- Experimental vendor workers are not started while RGB lab mode is disabled.
- Existing experimental mappings are preserved but clearly shown as disabled until the RGB lab is enabled again.
- Built-in update checks, Help & Feedback, questions/comments, bug reports and the optional one-time rating prompt are all retained.
- The MSI diagnostic/testing code remains available for development without cluttering the normal user experience.

## 0.3.12 Beta

- MSI native helper now waits 1.5 seconds after MLAPI_Initialize before MLAPI_GetDeviceInfo.
- MLAPI_GetDeviceInfo is retried up to five times when it returns an error instead of crashing.
- The helper now runs with the MSI SDK directory as its working directory.
- This specifically targets systems where MLAPI_Initialize succeeds but MSI's DLL terminates during immediate device enumeration.

## 0.3.11 Beta

- Added persistent stage-by-stage diagnostics around the native MSI SDK calls.
- Fixed MSI SDK reinstallation failing with Windows access denied while the DLL was still loaded.
- Removed the eager MSI scan before the first bridge command.
- MSI diagnostic UI now shows the last native stage, its code and detail.
- Native MSI helper is built with the static C++ runtime.

## 0.3.10 Beta

- Replaced the Python/ctypes MSI call path with a native Windows C++ helper.
- The helper initializes COM, loads MSI's official x64 Mystic Light SDK and calls the documented SDK exports with native Windows ABI types.
- The MSI helper remains isolated behind the watchdog, so a vendor SDK crash cannot freeze the main Raptor Link process.
- MSI diagnostics now include native-helper state and process exit code.

## 0.3.9 Diagnostic Beta

- Added low-level MSI Mystic Light diagnostics in the Sources RGB page.
- Shows DLL presence/path, MLAPI_Initialize result, MLAPI_GetDeviceInfo result, raw device types, raw LED counts, bridge restart count and last error.
- Fixed the stale “Choose an iCUE device” message while the MSI native source is selected.
- Intended to diagnose MSI motherboards that appear in MSI Center but are not returned to Raptor Link through the public SDK.

## 0.3.8 Beta

- Added a native MSI Mystic Light connector using MSI's official public SDK; OpenRGB is no longer required for MSI.
- Added in-app setup that downloads the official MSI SDK kit from MSI and extracts the x64 DLL into Raptor Link's local data folder instead of redistributing MSI's proprietary DLL.
- MSI devices/zones exposed by the SDK can be selected as RGB sources and their reported colors can feed WLED mappings.
- The MSI SDK runs in a disposable child process with a watchdog, so a hung MSI call should not block WLED, PC activity, iCUE or the Stop button.
- iCUE remains explicitly marked **Tested & Validated**; MSI is **Native Beta**.
- Other vendor integrations remain Beta through OpenRGB in this build.
- Added native MSI source regression tests and SDK installation tests.

> MSI's public SDK may not expose a true per-frame RGB value for every animated effect. Dynamic Mystic Light effects therefore remain part of the Beta validation effort.

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
