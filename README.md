# Orvibo Curtain Motor — Home Assistant Integration

A custom Home Assistant integration for Orvibo/HomeMate WiFi curtain motors. Controls your curtains via the Orvibo cloud protocol.

## Features

- **Open / Close / Stop** curtain
- **Set exact position** (0–100%)
- **Automatic polling** of curtain position
- **Auto-discovery** of regional server and family ID
- **Bundled TLS certificates** — fully self-contained
- **3-field setup** — just email, password, and device ID

## Installation

Copy the `custom_components/orvibo_curtain` folder into your Home Assistant config directory:

```
<ha-config>/custom_components/orvibo_curtain/
```

Restart Home Assistant.

## Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Orvibo Curtain**
3. Enter:
   - **Email** — your HomeMate/Cuco account email
   - **Password** — your account password
   - **Device ID** — your curtain's hardware UID (see below)

## Finding your Device ID

The device ID is the hardware UID of your curtain motor. You can find it by intercepting traffic from the HomeMate app using Frida:

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

