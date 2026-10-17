# Orvibo Curtain Motor — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

A custom Home Assistant integration for Orvibo/HomeMate WiFi curtain motors. Controls your curtains via the Orvibo cloud protocol.

## Installation

### HACS (Recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=kjanko&repository=orvibo-homeassistant-curtains&category=integration)

1. Open HACS in Home Assistant
2. Click the three dots in the top right → **Custom repositories**
3. Add `https://github.com/kjanko/orvibo-homeassistant-curtains` with category **Integration**
4. Search for "Orvibo Curtain" and install it
5. Restart Home Assistant

### Manual

Copy the `custom_components/orvibo_curtain` folder into your Home Assistant config directory:

```
<ha-config>/custom_components/orvibo_curtain/
```

Restart Home Assistant.

## Features

- **Open / Close / Stop** curtain
- **Set exact position** (0–100%)
- **Automatic polling** of curtain position
- **Auto-discovery** of regional server, home and devices
- **Configurable open position** — e.g. treat 90% as fully open, with the slider scaled to match
- **Re-authentication** prompt if your Orvibo password changes
- **Bundled TLS certificates** — fully self-contained


## Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Orvibo Curtain**
3. Sign in with your HomeMate/Cuco account **email** and **password**
4. If your account has several homes, pick the one with the curtain
5. Pick your curtain from the device list (known curtain motors are listed first)

If your curtain isn't in the list, choose **Enter device ID manually** (see below).

## Options

Under **Settings → Devices & Services → Orvibo Curtain → Configure**:

- **Invert position** — swap 0% and 100% if your motor reports them the other way round
- **Open position** — the motor position that counts as fully open (default 100%). The 0–100% slider is scaled to fit: with 90%, the curtain opens to 90% and shows as 100%, and 50% on the slider moves it to 45%.

## Finding your Device ID manually

Only needed if your curtain doesn't appear in the device list. The device ID is the hardware UID of your curtain motor. You can find it by intercepting traffic from the HomeMate app using Frida:

```bash
frida -U -f com.orvibo.homemate -l hook_before_encrypt.js
```

Look for the `uid` field in captured `[GSON]` lines. It's a 32-character hex string like `fa4bbdaf3c5e404abd34562f3c936694`.

## How it works

```
┌─────────────────┐       HTTPS        ┌─────────────────────┐
│  Config Flow     │◄──────────────────►│  Orvibo REST API    │
│  (discovery)     │  OAuth + family    │  (port 443)         │
└────────┬────────┘                     └─────────────────────┘
         │
         │  After setup:
         ▼
┌─────────────────┐     TLS 1.2        ┌─────────────────────┐
│  Coordinator     │◄──────────────────►│  Orvibo Binary API  │
│  (polling +      │   mutual auth      │  (port 10002)       │
│   commands)      │   AES-ECB JSON     │                     │
└─────────────────┘                     └─────────────────────┘
```

1. Discovers your regional server and family ID via the Orvibo REST API
2. Validates connectivity via the binary protocol (TLS mutual-auth + AES-128-ECB)
3. Polls curtain position periodically
4. Sends open/close/stop/position commands on demand

## Supported devices

- Orvibo/HomeMate WiFi curtain motors (device type 34)

## Troubleshooting

**"Could not connect to Orvibo cloud":**
- Verify your email/password work in the HomeMate/Cuco app
- Confirm the device ID is correct

**Entity shows as unavailable:**
- The Orvibo cloud may be temporarily unreachable
- Check HA logs for connection errors

**Position shows as "Unknown":**
- Normal on first start — the curtain only reports position when it moves
- Open or close it once and the position will populate

## License

MIT

