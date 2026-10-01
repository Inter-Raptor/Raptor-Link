# 🦖 Raptor Link

**Raptor Link** is a Windows application designed to connect **WLED** lighting to your PC and integrate it with the **Corsair iCUE** ecosystem.

It can also be used with WLED devices without Corsair hardware.

> Community project. Raptor Link is not affiliated with, endorsed by, or sponsored by Corsair or the WLED project. Corsair and iCUE are trademarks of their respective owners.

## ✨ Main features

- 🔗 Connect and manage multiple WLED controllers
- 🎮 Integration with Corsair iCUE
- 🖥️ Screen-to-LED mapping for ambient lighting
- 🖥️ Multi-monitor support
- 🎨 Use WLED presets as lighting sources
- ⚡ Trigger presets depending on PC activity/state
- 🌐 Open the web interface of each configured WLED device directly from Raptor Link
- 💡 Use Raptor Link with WLED even without Corsair hardware
- 🧩 Segment-based LED configuration

## 🧭 Screen mapping

Raptor Link is designed to make screen mapping visual and easy to understand.

You can position your monitors and define which WLED LED segments correspond to areas of the screen. This makes it possible to create Ambilight-style lighting even with complex LED installations.

Example:

```text
Monitor 1
┌──────────────────────────────┐
│  Segment A        Segment B  │
│                              │
│                              │
│  Segment D        Segment C  │
└──────────────────────────────┘

WLED:
Segment A → LEDs 0-24
Segment B → LEDs 25-74
Segment C → LEDs 75-124
Segment D → LEDs 125-149
```

## 📥 Download

The Windows installer will be available in the **Releases** section of this repository.

➡️ Go to: **Releases → Latest release → Assets**

The installer is added manually by the project owner.

## 🚀 Getting started

1. Install and configure WLED on your LED controller.
2. Make sure the PC and WLED devices are on the same local network.
3. Install Raptor Link.
4. Add your WLED device using its IP address.
5. Configure LED segments and screen mapping.
6. If desired, enable Corsair iCUE integration.
7. Select your lighting source, WLED preset, or screen capture mode.

## 🌈 WLED presets

Raptor Link can use presets already created inside WLED.

This allows you to keep your effects and colors configured in WLED while letting Raptor Link decide when they should be activated.

Typical uses include:

- preset when the PC is active
- preset when the screen capture mode is disabled
- decorative lighting
- fallback lighting
- gaming profiles

## 🎮 Corsair iCUE

The primary goal of Raptor Link is to bridge WLED installations with a Corsair iCUE-based PC setup.

The project aims to let DIY WLED lighting become part of a larger PC RGB environment instead of remaining isolated from commercial RGB hardware.

## 🛠️ Requirements

- Windows PC
- One or more WLED-compatible devices
- Local network access to the WLED devices
- Corsair iCUE only if you want to use the iCUE integration

## 🐛 Report a bug

If something does not work, open an issue using the **Bug report** template.

Please include:

- Raptor Link version
- Windows version
- WLED version
- number of WLED devices
- a description of the problem
- steps to reproduce it
- screenshots if useful

## 💡 Suggest a feature

Ideas are welcome.

Open an issue using the **Feature request** template and describe what you would like Raptor Link to do.

## 🗺️ Project roadmap

Raptor Link is actively evolving. Areas of development include:

- improved multi-monitor mapping
- easier visual segment placement
- additional WLED preset automation
- better iCUE synchronization
- device discovery and configuration improvements
- easier diagnostics and troubleshooting
- improved user interface

## 🤝 Contributing

Feedback, bug reports, testing and ideas are welcome.

See [CONTRIBUTING.md](CONTRIBUTING.md).

## 🔐 Security

Please do not publish sensitive network information such as private credentials or access tokens in issues.

See [SECURITY.md](SECURITY.md).

## 📜 License

No open-source license has been selected yet. Unless a license is added later, the project remains protected by standard copyright rules.

---

### Links

- [WLED project](https://github.com/Aircoookie/WLED)
- [Corsair iCUE](https://www.corsair.com/icue)
