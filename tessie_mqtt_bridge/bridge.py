"""Tessie Fleet Telemetry → MQTT bridge.

Connects to wss://streaming.tessie.com/<VIN>, parses the JSON envelope, and
republishes the user's selected fields to the local MQTT broker with HA
Discovery configs so the entities auto-create.

Designed to run identically in two modes:
- local (env vars from .env or shell)
- HA add-on (env vars set by run.sh from /data/options.json + supervisor mqtt service)

Adding support for a new streaming field is a one-line entry in FIELD_REGISTRY.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import sys
from math import atan2, cos, radians, sin, sqrt
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus

# Auto-load .env (committed defaults) then .env.local (gitignored secrets).
# Both are looked up next to this script, so CWD doesn't matter. In the HA
# add-on container these files don't exist; load_dotenv silently no-ops.
try:
    from dotenv import load_dotenv
    _HERE = Path(__file__).parent
    load_dotenv(_HERE / ".env")
    load_dotenv(_HERE / ".env.local", override=True)
except ImportError:
    pass

def _read_version() -> str:
    """Read the canonical version from sibling config.yaml so the bridge
    runtime log matches the add-on manifest. Works for both modes since
    the Dockerfile copies config.yaml into /app alongside bridge.py."""
    try:
        cfg = (Path(__file__).parent / "config.yaml").read_text()
        m = re.search(r'^version:\s*["\']?([^"\'\s]+)', cfg, re.MULTILINE)
        if m:
            return m.group(1)
    except OSError:
        pass
    return "unknown"


VERSION = _read_version()
log = logging.getLogger("tessie_bridge")


# ----------------------------- env / config -----------------------------------

def _env(name: str, default: str | None = None, *, required: bool = False) -> str:
    v = os.environ.get(name, default)
    if required and not v:
        log.error("required env var %s is not set", name)
        sys.exit(2)
    return v or ""


TESSIE_TOKEN = _env("TESSIE_TOKEN", required=True)
TESSIE_VIN = _env("TESSIE_VIN", required=True)
TESSIE_URL = _env("TESSIE_URL", "wss://streaming.tessie.com").rstrip("/")
MQTT_HOST = _env("MQTT_HOST", "localhost")
MQTT_PORT = int(_env("MQTT_PORT", "1883"))
MQTT_USERNAME = _env("MQTT_USERNAME", "")
MQTT_PASSWORD = _env("MQTT_PASSWORD", "")
MQTT_TOPIC_PREFIX = _env("MQTT_TOPIC_PREFIX", "tesla_stream").rstrip("/")
MQTT_DISCOVERY_PREFIX = _env("MQTT_DISCOVERY_PREFIX", "homeassistant").rstrip("/")
HOME_LATITUDE = float(_env("HOME_LATITUDE", "0") or "0")
HOME_LONGITUDE = float(_env("HOME_LONGITUDE", "0") or "0")
HOME_RADIUS_METERS = float(_env("HOME_RADIUS_METERS", "100") or "100")
LOG_LEVEL = _env("LOG_LEVEL", "INFO").upper()
TESSIE_FIELDS = _env("TESSIE_FIELDS", "Location,Gear,VehicleSpeed")


# ----------------------------- field registry ---------------------------------
#
# Each entry maps a Tessie/Tesla field name to its HA-side metadata.
# `handler` selects how the value is decoded and what entity type it becomes:
#
#   location        device_tracker (lat/lon JSON + home/not_home state)
#   shift_state     sensor (ShiftStateP/R/N/D → P/R/N/D, others → unknown)
#   numeric         sensor (numeric, with unit/device_class/state_class)
#   string          sensor (passthrough string)
#   enum_strip      sensor (strips the configured prefix from enum value)
#   boolean         binary_sensor (true/false → ON/OFF, optional invert)
#   enum_to_bool    binary_sensor (ON when value equals on_value, else OFF)
#
# Fields use Tesla's CamelCase names (verified against the streaming proto).
# When a user selects fields in HA UI, the names below are what they pick.

FIELD_REGISTRY: dict[str, dict[str, Any]] = {
    # --- location & motion ---
    "Location": {"handler": "location", "name": "Location", "icon": "mdi:car"},
    "VehicleSpeed": {
        "handler": "numeric", "name": "Speed", "unit": "mph",
        "device_class": "speed", "state_class": "measurement",
        "icon": "mdi:speedometer", "decimals": 1,
    },
    "GpsHeading": {
        "handler": "numeric", "name": "Heading", "unit": "°",
        "icon": "mdi:compass", "decimals": 0,
    },
    "Gear": {
        "handler": "shift_state", "name": "Shift State",
        "icon": "mdi:car-shift-pattern",
    },

    # --- battery & charging ---
    "Soc": {
        "handler": "numeric", "name": "Battery", "unit": "%",
        "device_class": "battery", "state_class": "measurement", "decimals": 1,
    },
    "EnergyRemaining": {
        "handler": "numeric", "name": "Energy Remaining", "unit": "kWh",
        "state_class": "measurement", "icon": "mdi:battery", "decimals": 2,
    },
    "RatedRange": {
        "handler": "numeric", "name": "Rated Range", "unit": "mi",
        "device_class": "distance", "state_class": "measurement",
        "icon": "mdi:map-marker-distance", "decimals": 1,
    },
    "ChargeLimitSoc": {
        "handler": "numeric", "name": "Charge Limit", "unit": "%",
        "device_class": "battery", "icon": "mdi:battery-charging",
    },
    "DetailedChargeState": {
        "handler": "enum_strip", "name": "Charge State",
        "prefix": "DetailedChargeState", "icon": "mdi:ev-station",
    },
    "ACChargingPower": {
        "handler": "numeric", "name": "AC Charging Power", "unit": "kW",
        "device_class": "power", "state_class": "measurement", "decimals": 2,
    },
    "DCChargingPower": {
        "handler": "numeric", "name": "DC Charging Power", "unit": "kW",
        "device_class": "power", "state_class": "measurement", "decimals": 2,
    },
    "ChargePortDoorOpen": {
        "handler": "boolean", "name": "Charge Port",
        "device_class": "opening", "icon": "mdi:ev-plug-tesla",
    },

    # --- odometer ---
    "Odometer": {
        "handler": "numeric", "name": "Odometer", "unit": "mi",
        "device_class": "distance", "state_class": "total_increasing",
        "icon": "mdi:counter", "decimals": 0,
    },

    # --- climate ---
    "InsideTemp": {
        "handler": "numeric", "name": "Inside Temperature", "unit": "°C",
        "device_class": "temperature", "state_class": "measurement", "decimals": 1,
    },
    "OutsideTemp": {
        "handler": "numeric", "name": "Outside Temperature", "unit": "°C",
        "device_class": "temperature", "state_class": "measurement", "decimals": 1,
    },
    "BatteryHeaterOn": {
        "handler": "boolean", "name": "Battery Heater",
        "device_class": "running", "icon": "mdi:fire",
    },

    # --- security & state ---
    "Locked": {
        # HA `lock` device_class: ON=unlocked, OFF=locked. Tesla reports
        # true=locked, so invert to align with HA semantics.
        "handler": "boolean", "name": "Locked", "device_class": "lock",
        "invert": True, "icon": "mdi:lock",
    },
    "SentryMode": {
        "handler": "enum_to_bool", "name": "Sentry Mode",
        "on_value": "SentryModeStateOn", "icon": "mdi:cctv",
    },
    "ValetModeEnabled": {
        "handler": "boolean", "name": "Valet Mode", "icon": "mdi:account-tie",
    },
    "DriverSeatOccupied": {
        "handler": "boolean", "name": "Driver Seat Occupied",
        "device_class": "occupancy", "icon": "mdi:car-seat",
    },
    "HomelinkNearby": {
        "handler": "boolean", "name": "Homelink Nearby",
        "icon": "mdi:garage-variant",
    },

    # --- software / metadata ---
    "Version": {
        "handler": "string", "name": "Software Version", "icon": "mdi:numeric",
    },
    "VehicleName": {
        "handler": "string", "name": "Vehicle Name", "icon": "mdi:car",
    },

    # --- tire pressure ---
    "TpmsPressureFl": {
        "handler": "numeric", "name": "Tire Pressure FL", "unit": "bar",
        "device_class": "pressure", "state_class": "measurement",
        "icon": "mdi:car-tire-alert", "decimals": 2,
    },
    "TpmsPressureFr": {
        "handler": "numeric", "name": "Tire Pressure FR", "unit": "bar",
        "device_class": "pressure", "state_class": "measurement",
        "icon": "mdi:car-tire-alert", "decimals": 2,
    },
    "TpmsPressureRl": {
        "handler": "numeric", "name": "Tire Pressure RL", "unit": "bar",
        "device_class": "pressure", "state_class": "measurement",
        "icon": "mdi:car-tire-alert", "decimals": 2,
    },
    "TpmsPressureRr": {
        "handler": "numeric", "name": "Tire Pressure RR", "unit": "bar",
        "device_class": "pressure", "state_class": "measurement",
        "icon": "mdi:car-tire-alert", "decimals": 2,
    },
}


def parse_selected_fields(raw: str) -> list[str]:
    selected: list[str] = []
    for f in (s.strip() for s in raw.split(",")):
        if not f:
            continue
        if f not in FIELD_REGISTRY:
            log.warning("unknown field %r in TESSIE_FIELDS — ignoring", f)
            continue
        if f not in selected:
            selected.append(f)
    return selected


SELECTED_FIELDS = parse_selected_fields(TESSIE_FIELDS)


# ----------------------------- helpers ----------------------------------------

_SLUG_PASS1 = re.compile(r"([a-z0-9])([A-Z])")
_SLUG_PASS2 = re.compile(r"([A-Z]+)([A-Z][a-z])")


def slug(camel: str) -> str:
    """`VehicleSpeed` → `vehicle_speed`, `ACChargingPower` → `ac_charging_power`."""
    s = _SLUG_PASS2.sub(r"\1_\2", _SLUG_PASS1.sub(r"\1_\2", camel))
    return s.lower()


def topic_for(field: str) -> str:
    return f"{MQTT_TOPIC_PREFIX}/{TESSIE_VIN}/{slug(field)}"


AVAILABILITY_TOPIC = f"{MQTT_TOPIC_PREFIX}/{TESSIE_VIN}/availability"
LOCATION_ATTRS_TOPIC = f"{MQTT_TOPIC_PREFIX}/{TESSIE_VIN}/location"
LOCATION_STATE_TOPIC = f"{MQTT_TOPIC_PREFIX}/{TESSIE_VIN}/location_state"


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000.0
    p1, p2 = radians(lat1), radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * R * atan2(sqrt(a), sqrt(1 - a))


def unwrap_value(v: Any) -> Any:
    """Tessie wraps each datum's value in a oneof type discriminator like
    `{"stringValue": "..."}` / `{"doubleValue": 65.5}` / `{"locationValue": {...}}`
    / `{"shiftStateValue": "ShiftStateP"}` / `{"sentryModeStateValue": "..."}`.
    For fields that aren't currently meaningful, Tessie sends `{"invalid": true}`.
    """
    if not isinstance(v, dict):
        return v
    if v.get("invalid") is True:
        return None
    for k, val in v.items():
        if k.endswith("Value") or k.endswith("_value"):
            return val
    return v


SHIFT_MAP = {
    "ShiftStateP": "P", "ShiftStateR": "R", "ShiftStateN": "N", "ShiftStateD": "D",
    "ShiftStateUnknown": "unknown", "ShiftStateInvalid": "unknown",
    "ShiftStateSNA": "unknown",
    "P": "P", "R": "R", "N": "N", "D": "D",
}


# ----------------------------- discovery payloads -----------------------------

def device_block() -> dict[str, Any]:
    return {
        "identifiers": [f"tesla_stream_{TESSIE_VIN}"],
        "name": "Tesla (Streaming)",
        "manufacturer": "Tesla",
        "model": "Fleet Telemetry via Tessie",
        "sw_version": VERSION,
    }


def _availability() -> list[dict[str, str]]:
    return [{
        "topic": AVAILABILITY_TOPIC,
        "payload_available": "online",
        "payload_not_available": "offline",
    }]


def _base_payload(field: str, meta: dict[str, Any]) -> dict[str, Any]:
    fid = slug(field)
    p: dict[str, Any] = {
        "name": meta.get("name", field),
        "object_id": f"tesla_stream_{fid}",
        "unique_id": f"tesla_stream_{TESSIE_VIN}_{fid}",
        "availability": _availability(),
        "device": device_block(),
    }
    if "icon" in meta:
        p["icon"] = meta["icon"]
    return p


def discovery_for(field: str, meta: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Return (component, config_topic, payload)."""
    base = MQTT_DISCOVERY_PREFIX
    node = f"tesla_stream_{TESSIE_VIN}"
    handler = meta["handler"]
    fid = slug(field)
    p = _base_payload(field, meta)

    if handler == "location":
        p.update({
            "state_topic": LOCATION_STATE_TOPIC,
            "json_attributes_topic": LOCATION_ATTRS_TOPIC,
            "source_type": "gps",
            "payload_home": "home",
            "payload_not_home": "not_home",
        })
        return "device_tracker", f"{base}/device_tracker/{node}/{fid}/config", p

    if handler in ("boolean", "enum_to_bool"):
        p.update({
            "state_topic": topic_for(field),
            "payload_on": "ON",
            "payload_off": "OFF",
        })
        if "device_class" in meta:
            p["device_class"] = meta["device_class"]
        return "binary_sensor", f"{base}/binary_sensor/{node}/{fid}/config", p

    # numeric / string / enum_strip / shift_state → sensor
    p["state_topic"] = topic_for(field)
    for k in ("unit", "device_class", "state_class"):
        if meta.get(k):
            p["unit_of_measurement" if k == "unit" else k] = meta[k]
    return "sensor", f"{base}/sensor/{node}/{fid}/config", p


def discovery_payloads() -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for field in SELECTED_FIELDS:
        meta = FIELD_REGISTRY[field]
        _, topic, payload = discovery_for(field, meta)
        out.append((topic, payload))

    # Always register the availability binary_sensor — it's how HA learns the
    # bridge is online/offline regardless of which fields are streamed.
    base = MQTT_DISCOVERY_PREFIX
    node = f"tesla_stream_{TESSIE_VIN}"
    out.append((
        f"{base}/binary_sensor/{node}/online/config",
        {
            "name": "Stream Online",
            "object_id": "tesla_stream_online",
            "unique_id": f"tesla_stream_{TESSIE_VIN}_online",
            "state_topic": AVAILABILITY_TOPIC,
            "payload_on": "online",
            "payload_off": "offline",
            "device_class": "connectivity",
            "device": device_block(),
        },
    ))
    return out


# ----------------------------- value handlers ---------------------------------

def _format_number(raw: Any, decimals: int | None) -> str | None:
    try:
        f = float(raw)
    except (TypeError, ValueError):
        return None
    if decimals is None:
        return f"{f}"
    return f"{f:.{decimals}f}"


# ----------------------------- bridge -----------------------------------------

class Bridge:
    def __init__(self) -> None:
        self.mqtt = mqtt.Client(
            client_id=f"tessie_stream_{TESSIE_VIN}",
            clean_session=True,
            protocol=mqtt.MQTTv311,
        )
        if MQTT_USERNAME:
            self.mqtt.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        self.mqtt.will_set(AVAILABILITY_TOPIC, "offline", qos=1, retain=True)
        self.mqtt.on_disconnect = self._on_mqtt_disconnect
        self.parse_error_streak = 0
        self.shutdown_event = asyncio.Event()

    @staticmethod
    def _on_mqtt_disconnect(client, userdata, rc):  # noqa: ANN001
        if rc != 0:
            log.warning("MQTT disconnected (rc=%s); paho will auto-reconnect", rc)

    def mqtt_start(self) -> None:
        log.info("connecting to MQTT %s:%s", MQTT_HOST, MQTT_PORT)
        self.mqtt.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        self.mqtt.loop_start()

    def publish_discovery(self) -> None:
        payloads = discovery_payloads()
        for t, payload in payloads:
            self.mqtt.publish(t, json.dumps(payload), qos=1, retain=True)
        log.info(
            "published %d discovery configs for %d selected fields: %s",
            len(payloads), len(SELECTED_FIELDS), ", ".join(SELECTED_FIELDS),
        )

    def publish_availability(self, state: str) -> None:
        self.mqtt.publish(AVAILABILITY_TOPIC, state, qos=1, retain=True)

    # --- per-handler dispatch ---

    def _handle_location(self, value: Any) -> None:
        loc = unwrap_value(value)
        if loc is None or not isinstance(loc, dict):
            return
        lat = loc.get("latitude")
        lon = loc.get("longitude")
        if lat is None or lon is None:
            return
        attrs = {"latitude": float(lat), "longitude": float(lon), "gps_accuracy": 5.0}
        self.mqtt.publish(LOCATION_ATTRS_TOPIC, json.dumps(attrs), qos=0, retain=True)
        if HOME_LATITUDE != 0.0 or HOME_LONGITUDE != 0.0:
            d = haversine_meters(float(lat), float(lon), HOME_LATITUDE, HOME_LONGITUDE)
            state = "home" if d <= HOME_RADIUS_METERS else "not_home"
            self.mqtt.publish(LOCATION_STATE_TOPIC, state, qos=0, retain=True)

    def _handle_shift_state(self, field: str, value: Any) -> None:
        raw = unwrap_value(value)
        if raw is None:
            return
        mapped = SHIFT_MAP.get(str(raw), "unknown")
        self.mqtt.publish(topic_for(field), mapped, qos=0, retain=True)

    def _handle_numeric(self, field: str, value: Any, meta: dict[str, Any]) -> None:
        raw = unwrap_value(value)
        if raw is None:
            return
        s = _format_number(raw, meta.get("decimals"))
        if s is not None:
            self.mqtt.publish(topic_for(field), s, qos=0, retain=True)
        else:
            log.debug("non-numeric value for %s: %r", field, raw)

    def _handle_string(self, field: str, value: Any) -> None:
        raw = unwrap_value(value)
        if raw is None:
            return
        self.mqtt.publish(topic_for(field), str(raw), qos=0, retain=True)

    def _handle_enum_strip(self, field: str, value: Any, meta: dict[str, Any]) -> None:
        raw = unwrap_value(value)
        if raw is None:
            return
        s = str(raw)
        prefix = meta.get("prefix", "")
        if prefix and s.startswith(prefix):
            s = s[len(prefix):]
        self.mqtt.publish(topic_for(field), s, qos=0, retain=True)

    def _handle_boolean(self, field: str, value: Any, meta: dict[str, Any]) -> None:
        raw = unwrap_value(value)
        if raw is None:
            return
        truthy = bool(raw)
        if meta.get("invert"):
            truthy = not truthy
        self.mqtt.publish(topic_for(field), "ON" if truthy else "OFF", qos=0, retain=True)

    def _handle_enum_to_bool(self, field: str, value: Any, meta: dict[str, Any]) -> None:
        raw = unwrap_value(value)
        if raw is None:
            return
        on_value = meta.get("on_value")
        truthy = (str(raw) == on_value)
        self.mqtt.publish(topic_for(field), "ON" if truthy else "OFF", qos=0, retain=True)

    def handle_datum(self, key: str, value: Any) -> None:
        if key not in SELECTED_FIELDS:
            return
        meta = FIELD_REGISTRY[key]
        h = meta["handler"]
        if h == "location":
            self._handle_location(value)
        elif h == "shift_state":
            self._handle_shift_state(key, value)
        elif h == "numeric":
            self._handle_numeric(key, value, meta)
        elif h == "string":
            self._handle_string(key, value)
        elif h == "enum_strip":
            self._handle_enum_strip(key, value, meta)
        elif h == "boolean":
            self._handle_boolean(key, value, meta)
        elif h == "enum_to_bool":
            self._handle_enum_to_bool(key, value, meta)
        else:
            log.warning("no handler %r for field %s", h, key)

    def handle_message(self, msg: dict[str, Any]) -> None:
        if "errors" in msg:
            log.warning("Tessie reported field errors: %s", msg.get("errors"))
        if "status" in msg:
            log.info("Tessie status: %s", msg.get("status"))
        data = msg.get("data") or []
        if not isinstance(data, list):
            return
        had_data = False
        for datum in data:
            try:
                key = datum.get("key")
                if not key:
                    continue
                self.handle_datum(key, datum.get("value", {}))
                had_data = True
            except Exception:  # noqa: BLE001
                log.exception("error handling datum: %s", datum)
                self.parse_error_streak += 1
                if self.parse_error_streak >= 5:
                    log.warning(
                        "5+ consecutive parse errors — possible Tessie schema drift; "
                        "verify field structure against developer.tessie.com"
                    )
                return
        if had_data:
            self.parse_error_streak = 0

    async def run_websocket_once(self) -> None:
        url = f"{TESSIE_URL}/{TESSIE_VIN}"
        log.info("connecting to %s", url)
        async with websockets.connect(
            url,
            extra_headers={"Authorization": f"Bearer {TESSIE_TOKEN}"},
            ping_interval=30,
            ping_timeout=30,
            close_timeout=5,
            max_size=2_000_000,
        ) as ws:
            self.publish_availability("online")
            log.info("connected to Tessie stream")
            async for raw in ws:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    log.warning("non-JSON message: %r", raw[:200])
                    continue
                self.handle_message(msg)

    async def run(self) -> int:
        self.mqtt_start()
        await asyncio.sleep(0.5)
        self.publish_discovery()
        backoff = 1
        try:
            while not self.shutdown_event.is_set():
                try:
                    await self.run_websocket_once()
                    backoff = 1
                except InvalidStatus as e:
                    code = getattr(getattr(e, "response", None), "status_code", None)
                    if code == 401:
                        log.error("Tessie rejected the token (401). Check TESSIE_TOKEN.")
                        return 2
                    log.warning("Tessie WS handshake error: %s", e)
                except ConnectionClosed as e:
                    log.info("Tessie WS closed (code=%s reason=%r) — likely car sleeping", e.code, e.reason)
                except OSError as e:
                    log.warning("network error: %s", e)
                except Exception:  # noqa: BLE001
                    log.exception("unexpected error in WS loop")
                self.publish_availability("offline")
                if self.shutdown_event.is_set():
                    break
                log.info("reconnecting in %ds", backoff)
                try:
                    await asyncio.wait_for(self.shutdown_event.wait(), timeout=backoff)
                    break
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 60)
        finally:
            self.publish_availability("offline")
            await asyncio.sleep(0.2)
            self.mqtt.loop_stop()
            self.mqtt.disconnect()
        return 0


# ----------------------------- entrypoint -------------------------------------

def _validate_config() -> None:
    if not TESSIE_TOKEN or len(TESSIE_TOKEN) < 16:
        log.error("TESSIE_TOKEN missing or implausibly short")
        sys.exit(2)
    if len(TESSIE_VIN) != 17:
        log.error("TESSIE_VIN must be a 17-character VIN, got %r", TESSIE_VIN)
        sys.exit(2)
    if not SELECTED_FIELDS:
        log.error("no valid fields in TESSIE_FIELDS=%r — nothing to publish", TESSIE_FIELDS)
        sys.exit(2)
    if "Location" in SELECTED_FIELDS and HOME_LATITUDE == 0.0 and HOME_LONGITUDE == 0.0:
        log.warning(
            "HOME_LATITUDE/HOME_LONGITUDE not set — bridge will publish raw lat/lon "
            "but will not compute home/not_home state. Garage automation may not trigger."
        )


def main() -> None:
    logging.basicConfig(
        level=LOG_LEVEL,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )
    log.info("tessie_mqtt_bridge v%s starting (VIN=%s)", VERSION, TESSIE_VIN)
    _validate_config()

    bridge = Bridge()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _signal(name: str) -> None:
        log.info("received %s — shutting down", name)
        bridge.shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal, sig.name)

    sys.exit(loop.run_until_complete(bridge.run()))


if __name__ == "__main__":
    main()
