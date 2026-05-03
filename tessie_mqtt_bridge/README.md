# tessie_mqtt_bridge — local-run guide

This is the same code that ships as the HA add-on. Use this guide to test the bridge from your Mac (or any machine on your LAN) before installing the add-on.

## Env-file convention

Two files, Next.js style:

| File | Committed? | Role |
|---|---|---|
| `.env` | yes | Vanilla defaults — endpoints, ports, topic prefixes, log level. Safe to push. |
| `.env.local` | no (gitignored) | Real credentials — Tessie token, VIN, MQTT creds, home zone. Yours only. |

Both are auto-loaded by `bridge.py` and `probe_tessie.py` at startup (via `python-dotenv`). Loading order is `.env` → `.env.local`, so anything in `.env.local` overrides the committed defaults.

If you don't have a `.env.local` yet, `cp .env .env.local` and edit. The bridge runs identically without one — it'll just complain that required vars are unset.

## 1. Install dependencies

```bash
cd tessie_mqtt_bridge
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Requires Python 3.9+.

## 2. Fill in `.env.local`

The committed `.env` already has sensible defaults. Open `.env.local` and set:

- **`TESSIE_TOKEN`** — paste from [tessie.com → Developer Settings](https://my.tessie.com/settings/api).
- **`TESSIE_VIN`** — your 17-char Tesla VIN.
- **`MQTT_HOST`** — your HA box's LAN IP (e.g. `192.168.1.10`).
- **`MQTT_USERNAME` / `MQTT_PASSWORD`** — any HA user with MQTT access. If you don't have one yet, in HA: **Settings → People → Users → Add User** (check "Local access only", give it a memorable name like `mqtt_bridge`). Mosquitto authenticates against HA's user list automatically.
- **`HOME_LATITUDE` / `HOME_LONGITUDE`** — read off HA → **Settings → Areas → Zones → Home**. Without these, the bridge publishes raw lat/lon but won't drive `home`/`not_home`. Only relevant if `Location` is in `TESSIE_FIELDS`.

Override any default from `.env` by adding it to `.env.local` (e.g., a different `TESSIE_FIELDS` list, a custom `LOG_LEVEL=DEBUG`, etc.).

## 3. Run

```bash
python bridge.py
```

That's it — the script auto-loads both env files. You should see:

```
INFO    tessie_bridge tessie_mqtt_bridge v0.2.0 starting (VIN=7SAY...)
INFO    tessie_bridge connecting to MQTT 192.168.1.10:1883
INFO    tessie_bridge published 4 discovery configs for 3 selected fields: Location, Gear, VehicleSpeed
INFO    tessie_bridge connecting to wss://streaming.tessie.com/7SAY...
INFO    tessie_bridge connected to Tessie stream
```

For the WebSocket-only probe (no MQTT, no HA — just verify Tessie auth/streaming):

```bash
python probe_tessie.py --seconds 60
```

## 4. Verify on the MQTT side

In another terminal:

```bash
# Discovery configs (one-time, retained)
mosquitto_sub -h <HA_IP> -u <user> -P <pass> \
  -t 'homeassistant/+/tesla_stream_+/+/config' -v

# Live data
mosquitto_sub -h <HA_IP> -u <user> -P <pass> -t 'tesla_stream/#' -v
```

When the car is awake, you'll see your selected fields updating. When the car is asleep, `tesla_stream/<vin>/availability` flips to `offline` and the bridge retries with backoff.

## 5. Verify in Home Assistant

Within ~10s of the bridge starting, **Settings → Devices & Services** shows a device "Tesla (Streaming)" containing one entity per field you selected, plus `binary_sensor.tesla_stream_online`. With the default `TESSIE_FIELDS=Location,Gear,VehicleSpeed`:

- `device_tracker.tesla_stream_location`
- `sensor.tesla_stream_gear` (friendly name "Shift State")
- `sensor.tesla_stream_vehicle_speed` (friendly name "Speed")
- `binary_sensor.tesla_stream_online`

Drive the car around the block — `device_tracker.tesla_stream_location` should flip to `home` within ~2s of crossing the home zone radius.

## 6. Stop the bridge

`Ctrl-C`. The bridge publishes `availability=offline` before exiting; the entities go unavailable in HA but the device stays registered.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `TESSIE_TOKEN missing or implausibly short` | Set it in `.env.local`. |
| `Tessie rejected the token (401)` | Token invalid or expired. Regenerate at tessie.com. |
| Bridge connects but no data | Fleet Telemetry not enabled in Tessie console for this car, or no fields selected there. |
| `device_tracker.tesla_stream_location` shows `unknown` | `HOME_LATITUDE`/`HOME_LONGITUDE` not set in `.env.local`. |
| HA doesn't show new device | Discovery prefix mismatch with HA MQTT integration's prefix (default `homeassistant`). |
| Lots of `5+ consecutive parse errors` | Tessie schema drift — verify field structure at `developer.tessie.com`. |
| Connection drops every few minutes | Normal — car went to sleep. Bridge auto-reconnects. |

## Once happy → install as add-on

Push this repo to GitHub and follow the install steps in the [top-level README](../README.md#installing-as-a-home-assistant-add-on). The `.env*` files don't get used in the add-on — Supervisor injects MQTT creds and the home zone is auto-fetched from `zone.home`.
