# Changelog

## 0.2.2 — 2026-05-03

- Bridge now reads its `VERSION` from `config.yaml`, so the manifest is the single source of truth and the runtime log line always matches the installed add-on version. Dockerfile copies `config.yaml` into `/app` so the same code path works in both local and add-on modes.

## 0.2.1 — 2026-05-03

- Fix: home-zone auto-detect now triggers correctly when `home_latitude` / `home_longitude` are left at the default. The previous string comparison against `"0"` missed `bashio::config`'s `"0.0"` float form, so the add-on was skipping `zone.home` lookup and treating 0,0 as a real override (location entity worked but `home`/`not_home` never flipped).

## 0.2.0 — 2026-05-02

- Generalized: any user can paste their own Tessie token + VIN.
- Multi-select **Fields** option in the add-on UI lets users pick which streaming fields to expose to HA. Curated registry of ~27 fields covering location, motion, battery, charging, climate, security, software, tires, and odometer.
- Each field maps to an appropriate HA entity (`device_tracker` / `sensor` / `binary_sensor`) with unit / device_class / state_class.
- Defensive: drops `{"invalid": true}` Tessie payloads instead of publishing garbage.
- Generic enum-value unwrapping (`*Value` / `*_value`) so all enum types decode correctly without per-field code.

## 0.1.0 — 2026-05-02

- Initial release.
- Streams Tessie Fleet Telemetry WebSocket to local MQTT.
- HA Discovery for `device_tracker`, two sensors, and an availability binary sensor.
- Auto-detects home zone via supervisor API.
- Exponential-backoff reconnect on car-asleep disconnects.
