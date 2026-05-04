# Tessie MQTT Bridge

[![lint](https://github.com/kapliy/tessie-mqtt-bridge/actions/workflows/lint.yml/badge.svg?branch=main)](https://github.com/kapliy/tessie-mqtt-bridge/actions/workflows/lint.yml)

Republishes Tessie Fleet Telemetry (WebSocket) to the local MQTT broker so Home Assistant gets sub-second Tesla updates. Built as a Home Assistant custom add-on, with a local-run mode for testing.

The official `tessie` core integration polls and lags real arrival by tens of seconds (sometimes minutes when the car is asleep). This bridge runs in parallel and creates a separate device "Tesla (Streaming)" with the streaming fields you select.

You pick fields via a multi-select in the add-on UI (or a CSV env var locally). Defaults are `Location, Gear, VehicleSpeed` — enough for fast garage-on-arrival. The full menu covers ~27 fields across location/motion, battery/charging, climate, security, software, tires, and odometer; each one maps to an appropriate `device_tracker`, `sensor`, or `binary_sensor` with the right unit/device_class.

The official integration's entities are **not** modified.

## Two ways to run

| Mode | When to use | Config |
|---|---|---|
| **Local** | First-time testing, before installing the add-on | `.env` file, `python bridge.py` |
| **HA add-on** | Production | Configure via HA UI; MQTT credentials auto-injected by Supervisor |

## Prerequisites (Tessie console — one-time)

1. Go to [tessie.com](https://tessie.com) → vehicle → **Settings** → **Fleet Telemetry**.
2. Enable streaming and select these fields at minimum:
   - `Location`
   - `Gear`
   - `VehicleSpeed` (optional but useful)
3. From **Developer Settings**, copy the access token.
4. Note the 17-char VIN (you'll paste it into the add-on Configuration tab or `.env.local`).

## Prerequisites (Home Assistant side)

This add-on runs only on **Home Assistant OS** or **HA Supervised**. It won't
work on HA Container or HA Core — those don't support Supervisor add-ons.

You also need an MQTT broker registered with Supervisor before this add-on
will install — Supervisor refuses to start `services: mqtt:need` add-ons
without one.

If you haven't set MQTT up yet:

1. **Install the Mosquitto broker add-on.** Settings → Add-ons → Add-on store →
   "Mosquitto broker" → Install → Start. Recommended: toggle Watchdog and
   Start-on-boot ON.
2. **Configure the MQTT integration.** Settings → Devices & Services. HA
   usually auto-discovers Mosquitto once it's running and offers it as a
   "Discovered" integration — click Configure and accept the defaults. If it
   doesn't auto-discover, click **+ Add Integration** → **MQTT** → enter
   `core-mosquitto` as the broker host.

When you run this **as the add-on** (production), Supervisor creates a
dedicated MQTT user for the add-on and injects its credentials — you never
see or manage them. If you're running locally for testing, see
[tessie_mqtt_bridge/README.md](tessie_mqtt_bridge/README.md#2-fill-in-envlocal)
for how to add an HA user that Mosquitto can authenticate against.

## Local testing

See [tessie_mqtt_bridge/README.md](tessie_mqtt_bridge/README.md) for the local-run guide.

Quick version:

```bash
cd tessie_mqtt_bridge
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env .env.local  # then edit .env.local with your TESSIE_TOKEN, VIN, MQTT creds
python bridge.py
```

Env-file convention: `.env` is committed (vanilla defaults), `.env.local` is gitignored (your real credentials). Both auto-load at startup; `.env.local` wins.

In another terminal, watch the discovery configs and live data:

```bash
mosquitto_sub -h <ha-host> -u <user> -P <pass> -t 'homeassistant/+/tesla_stream_+/+/config' -v
mosquitto_sub -h <ha-host> -u <user> -P <pass> -t 'tesla_stream/#' -v
```

Within ~10s, HA's **Settings → Devices & Services** should show a new device "Tesla (Streaming)".

## Installing as a Home Assistant add-on

> Complete **Prerequisites (Home Assistant side)** above first — MQTT must
> already be set up or the install will fail with an "unknown error" from
> Supervisor.

1. Push this repo to GitHub (e.g. `kapliy/tessie-mqtt-bridge`).
2. In HA: **Settings → Add-ons → ⋮ (top right) → Repositories** → paste the repo URL → **Add**.
3. The "Tessie MQTT Bridge" add-on appears in the store. Click **Install**.
4. Open the add-on's **Configuration** tab. Paste the Tessie token and your 17-char VIN. Topic prefix and home radius defaults work for most users.
5. Start the add-on. Watch the **Log** tab to confirm it connects to Tessie and the broker.

The add-on is fully GUI-configurable — no YAML editing required.

## Files

- [`tessie_mqtt_bridge/`](tessie_mqtt_bridge/) — the add-on (also runs locally)
- [`repository.yaml`](repository.yaml) — makes this folder a custom add-on repository
