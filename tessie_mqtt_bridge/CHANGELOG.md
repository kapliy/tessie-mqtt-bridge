# Changelog

## 0.2.7 — 2026-05-08

- **`expire_after_seconds` default changed from 600 to 0** (disabled). The 0.2.5 default of 600s caused per-entity `unavailable` flapping for slow-changing fields like `DetailedChargeState` (Tessie heartbeat cadence > 10min between events) and `Location` (when the car sleeps). Triggers using `not_from: [unavailable, unknown]` — the standard guard against retained-value replay false-fires — were suppressed on the recovery `unavailable → <state>` transition, breaking real unplug/arrival detection. Pre-0.2.5 had no `expire_after` and these triggers worked in real time; 0.2.7 restores that behavior. Users who want staleness detection can opt in via the add-on Configuration tab. **Existing installs need to manually set `expire_after_seconds` to 0 in the Configuration tab — Supervisor preserves user-saved values across updates and won't pick up the new default automatically.**

## 0.2.6 — 2026-05-08

- Bootstrap retry for the supervisor zone.home fetch in `run.sh`. At HA boot the Core API can briefly return 502 while components are still loading; previously a single transient failure left the bridge running with no home zone for the rest of its lifetime, silently breaking the location-based automation. Now retries up to 6 times with 5s sleeps before falling through to the warning. The warning now also tells the user to use the manual `home_latitude`/`home_longitude` override as a workaround.

## 0.2.5 — 2026-05-07

Robustness improvements driven by a real false-fire on the unplug-garage automation.

- **Offline-grace period.** New `offline_grace_seconds` option (default 30). Tessie's server idle-closes the WebSocket every ~60 minutes (code 1006); the bridge reconnects within ~1 second. Previously the bridge published `availability=offline` immediately on every such close, causing HA to flip every entity to `unavailable` and back, which can falsely fire `to:` state-change triggers on the replay of retained values. With grace > 0, the offline publish is delayed and cancelled if the next reconnect succeeds in time. Routine idle closes become invisible to HA.
- **`expire_after` in discovery.** New `expire_after_seconds` option (default 600). Adds the `expire_after` field to per-entity MQTT discovery payloads so HA marks entities `unavailable` once their data is stale, regardless of the bridge's availability topic. Backstops the offline-grace mechanism for the genuine "data is stale" case (car asleep at work for hours, etc.) — automations triggering on `to: <state>` won't see retained replays as fresh state changes.
- **Backoff reset bug fix.** Reconnect backoff now resets to 1s the moment a WebSocket connection is established, not only when `run_websocket_once` returns without raising. Tessie's idle-close raises `ConnectionClosed`, so the previous code never reset and backoff saturated at 60s permanently. Reconnects after routine idle closes are now ~1s instead of 60s.

## 0.2.4 — 2026-05-03

- `sensor.tesla_streaming_shift_state` is now declared as an HA enum with `device_class: enum` and `options: [P, R, N, D, unknown]` in the discovery payload. Prettier UI display, no functional change for existing automations — entity_id and state values are unchanged. The discovery builder now generally forwards an `options` field through to HA so future enum sensors can reuse it.

## 0.2.3 — 2026-05-03

- New `device_name` add-on option (default `Tesla (Streaming)`). Lets users with multiple cars (or anyone who doesn't love the parens in the slug) override the device label and the slug-derived entity IDs. The stable VIN-based device identifier is unchanged so existing entities aren't orphaned by a rename.

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
