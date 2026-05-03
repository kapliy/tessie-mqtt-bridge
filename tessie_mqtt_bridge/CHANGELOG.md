# Changelog

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
