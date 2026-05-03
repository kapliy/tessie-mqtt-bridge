# Tessie MQTT Bridge

Republishes Tessie Fleet Telemetry (WebSocket) to the local MQTT broker so Home Assistant gets sub-second Tesla updates. Built as a Home Assistant custom add-on, with a local-run mode for testing.

The official `tessie` core integration polls and lags real arrival by tens of seconds (sometimes minutes when the car is asleep). This bridge runs in parallel and creates a separate device "Tesla (Streaming)" with the streaming fields you select.

You pick fields via a multi-select in the add-on UI (or a CSV env var locally). Defaults are `Location, Gear, VehicleSpeed` — enough for fast garage-on-arrival. The full menu covers ~27 fields across location/motion, battery/charging, climate, security, software, tires, and odometer; each one maps to an appropriate `device_tracker`, `sensor`, or `binary_sensor` with the right unit/device_class.

The official integration's entities are **not** modified.

## Two ways to run

| Mode | When to use | Config |
|---|---|---|
| **Local** | First-time testing, before installing the add-on | `.env` file, `python bridge.py` |
| **HA add-on** | Production | Configure via HA UI; MQTT credentials auto-injected by Supervisor |

## Prerequisites (one-time, in Tessie console)

1. Go to [tessie.com](https://tessie.com) → vehicle → **Settings** → **Fleet Telemetry**.
2. Enable streaming and select these fields at minimum:
   - `Location`
   - `Gear`
   - `VehicleSpeed` (optional but useful)
3. From **Developer Settings**, copy the access token.
4. Note the 17-char VIN (you'll paste it into the add-on Configuration tab or `.env.local`).

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

1. Push this repo to GitHub (e.g. `kapliy/tessie-mqtt-bridge`).
2. In HA: **Settings → Add-ons → ⋮ (top right) → Repositories** → paste the repo URL → **Add**.
3. The "Tessie MQTT Bridge" add-on appears in the store. Click **Install**.
4. Open the add-on's **Configuration** tab. Paste the Tessie token and your 17-char VIN. Topic prefix and home radius defaults work for most users.
5. Start the add-on. Watch the **Log** tab to confirm it connects to Tessie and the broker.

The add-on is fully GUI-configurable — no YAML editing required.

## Files

- [`tessie_mqtt_bridge/`](tessie_mqtt_bridge/) — the add-on (also runs locally)
- [`repository.yaml`](repository.yaml) — makes this folder a custom add-on repository
