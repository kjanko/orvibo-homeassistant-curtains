# Orvibo Curtain — Home Assistant Integration

A self-contained custom integration that exposes your Orvibo/HomeMate WiFi curtain motor as a `cover` entity in Home Assistant.

## What you need

1. Your **Orvibo/HomeMate account email**
2. Your **account password**
3. Your **device ID** (found in the HomeMate/Cuco app under device info, or captured via Frida — see the main project README)

That's it. The integration discovers your regional server, family ID, and device UID automatically. TLS client certificates are bundled.

## Installation

Copy the `custom_components/orvibo_curtain` folder into your Home Assistant config directory:

```
<ha-config>/custom_components/orvibo_curtain/
```

Then restart Home Assistant.

## Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Orvibo Curtain**
3. Enter your email, password, and device ID
4. The integration will:
   - Log in to the Orvibo REST API to discover your device metadata
   - Validate the binary protocol connection (TLS mutual-auth on port 10002)
   - Create a cover entity with open/close/stop/set-position controls

## Features

- **Open / Close / Stop** curtain
- **Set exact position** (0–100%)
- **Polls position** from the Orvibo cloud every 60 seconds
- **Optimistic updates** — UI reflects commands immediately
- **Proper device registry** — shows as an Orvibo curtain device in HA
- **Self-contained** — bundled TLS certificates, automatic server discovery

## How it works

```
┌─────────────────┐       HTTPS        ┌─────────────────────┐
│  Config Flow     │◄──────────────────►│  Orvibo REST API    │
│  (discovery)     │    OAuth + query    │  (port 443)         │
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

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Integration setup/teardown |
| `api.py` | Protocol client (REST discovery + binary socket) |
| `config_flow.py` | 3-field setup wizard |
| `coordinator.py` | Polling coordinator with position caching |
| `cover.py` | Cover entity with optimistic updates |
| `const.py` | Constants and config keys |
| `manifest.json` | HA integration metadata |
| `strings.json` | UI text |
| `translations/en.json` | English translations |
| `orvibo_client_cert.pem` | Bundled TLS client certificate |
| `orvibo_client_key.pem` | Bundled TLS client private key |

## Troubleshooting

**"Could not connect to Orvibo cloud":**
- Verify your email/password work in the HomeMate app
- Confirm the device ID is correct (it's a long hex string like `z19e917382b14aabbe7a7754b0015464`)

**Entity shows as unavailable:**
- The Orvibo cloud may be temporarily unreachable
- Check Home Assistant logs for connection errors

**Position shows as "Unknown":**
- Normal on first start — the curtain only reports position when it moves
- Try opening/closing it once and the position will populate
