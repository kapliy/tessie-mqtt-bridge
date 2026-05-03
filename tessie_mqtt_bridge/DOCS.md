# Tessie MQTT Bridge

Bridges Tessie Fleet Telemetry (WebSocket) → local MQTT broker so Home Assistant gets sub-second Tesla updates. Runs alongside the official `tessie` integration; doesn't modify any of its entities.

## What it creates

A device "Tesla (Streaming)" containing one entity per field you selected, plus a connectivity sensor. Entity types are auto-derived from the field's data type:

| Field type | HA entity | Examples |
|---|---|---|
| GPS location | `device_tracker` | Location |
| Numeric | `sensor` | Speed, Battery, Range, Odometer, Inside Temp |
| Enum string | `sensor` | Shift State, Charge State |
| Boolean | `binary_sensor` | Locked, Charge Port, Battery Heater |
| Connectivity | `binary_sensor` | Stream Online (always created) |

## Before you start

1. In Tessie console (tessie.com → vehicle → **Settings → Fleet Telemetry**), **enable streaming** and select the Tesla-side fields you care about. The bridge supports a curated list (see "Fields" below); if Tessie isn't streaming a field you select in the add-on, the entity will simply stay unavailable.
2. From **Developer Settings**, copy the access token.
3. Note the 17-char VIN.

## Configuration (HA UI)

All fields are editable in the add-on's **Configuration** tab.

| Field | Required | Default | Notes |
|---|---|---|---|
| **Tessie Token** | yes | — | Bearer token from Tessie. Stored as a password. |
| **Tessie VIN** | yes | — | 17-char Tesla VIN. |
| **Fields** | yes | `Location, Gear, VehicleSpeed` | Multi-select. Click "Add" to add each field. |
| **Topic Prefix** | no | `tesla_stream` | MQTT topic root. |
| **Discovery Prefix** | no | `homeassistant` | Match your HA MQTT integration's discovery prefix. |
| **Home Radius (m)** | no | `100` | Distance threshold for `home` state. The add-on auto-uses your `zone.home` radius if you leave this at the default. |
| **Log Level** | no | `info` | `debug` for troubleshooting. |
| **Tessie URL** | no | `wss://streaming.tessie.com` | Advanced: override the WebSocket endpoint. Useful for testing or if Tessie ever moves regions. |
| **Home Latitude** | no | `0` (auto) | Advanced: manual override for home zone latitude. Leave at 0 to auto-detect from `zone.home`. |
| **Home Longitude** | no | `0` (auto) | Advanced: manual override for home zone longitude. Leave at 0 to auto-detect from `zone.home`. |

The MQTT broker host/port/credentials are **not** shown — they're auto-injected by Supervisor via `services: mqtt:need`. Supervisor creates a dedicated MQTT user for the add-on; you don't manage it.

Home zone resolution priority: **manual override** (if both `home_latitude` and `home_longitude` are non-zero) → **`zone.home` auto-detect** via supervisor API → **disabled** (raw lat/lon still publishes; no home/not_home state).

## Fields

Picked from a curated registry in `bridge.py`. Each field maps to an HA entity with appropriate unit/device_class/state_class.

**Location & motion**
- `Location` — `device_tracker.tesla_stream_location` (home/not_home + lat/lon attrs)
- `VehicleSpeed`, `GpsHeading`, `Gear`

**Battery & charging**
- `Soc`, `EnergyRemaining`, `RatedRange`, `ChargeLimitSoc`, `DetailedChargeState`, `ACChargingPower`, `DCChargingPower`, `ChargePortDoorOpen`

**Climate**
- `InsideTemp`, `OutsideTemp`, `BatteryHeaterOn`

**Security & state**
- `Locked` (inverted to match HA's lock convention), `SentryMode`, `ValetModeEnabled`, `DriverSeatOccupied`, `HomelinkNearby`

**Software / metadata**
- `Version`, `VehicleName`

**Tire pressure**
- `TpmsPressureFl`, `TpmsPressureFr`, `TpmsPressureRl`, `TpmsPressureRr`

**Odometer**
- `Odometer`

To add more fields, append to `FIELD_REGISTRY` in `bridge.py` and add the field name to the `list(...)` in `config.yaml`'s `schema` block. (One-line entry per field.)

## Behavior

- Connects on startup, publishes Discovery configs (retained), publishes `availability=online`.
- For each selected field present in a Tessie message, publishes the formatted value (retained) — so HA picks up the last value after restart.
- `{"invalid": true}` from Tessie is silently dropped (no garbage messages).
- Reconnects with exponential backoff (1s → 60s) when the car sleeps and the WebSocket closes — that's normal, not an error.
- Exits non-zero on a bad token (401) so Supervisor surfaces a clear failure rather than looping forever.
- Survives malformed messages (logs and skips); after 5+ consecutive parse errors logs a schema-drift warning.

## Verifying it works

After starting:

1. **Add-on Log tab** — should show `published N discovery configs for M selected fields: Location, Gear, …` and then `connected to Tessie stream`.
2. **Settings → Devices & Services** — new device "Tesla (Streaming)" with one entity per selected field, plus "Stream Online".
3. Drive the car. `device_tracker.tesla_stream_location` should flip to `home` within ~2s of crossing the home zone (assuming `Location` is selected).

## Updating existing automations

Once the streaming entities are verified, point any latency-sensitive automations (e.g. garage-on-arrival) at them instead of the polling integration's entities — `device_tracker.tesla_stream_location` triggers within ~2s instead of tens of seconds. The polling integration's entities stay registered and continue working as a backup.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| 401 in log | Tessie token invalid or expired — regenerate at tessie.com. |
| No data flowing | Fleet Telemetry not enabled / no Tesla-side fields selected in Tessie console. |
| Some entities never update | Tessie isn't streaming that field. Check Tessie console → Fleet Telemetry. |
| `home`/`not_home` not flipping | Supervisor API call to `zone.home` failed — check log for `Could not fetch zone.home`. |
| Entities don't appear | Discovery prefix mismatch with your HA MQTT integration. |
| Bridge restarts and entities show "Unknown" briefly | Normal — retained state hasn't been received yet from the broker. |
