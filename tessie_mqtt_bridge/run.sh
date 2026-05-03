#!/usr/bin/with-contenv bashio
# Translates HA add-on options + Supervisor-injected services into env vars,
# then execs bridge.py. Bridge code is identical to the local-run path.
set -e

# --- add-on options (UI-configurable) -----------------------------------------
export TESSIE_TOKEN="$(bashio::config 'tessie_token')"
export TESSIE_VIN="$(bashio::config 'tessie_vin')"
export TESSIE_URL="$(bashio::config 'tessie_url')"
export MQTT_TOPIC_PREFIX="$(bashio::config 'topic_prefix')"
export MQTT_DISCOVERY_PREFIX="$(bashio::config 'discovery_prefix')"
export HOME_RADIUS_METERS="$(bashio::config 'home_radius_meters')"
export LOG_LEVEL="$(bashio::config 'log_level' | tr '[:lower:]' '[:upper:]')"

# `fields` is a list — collapse to comma-separated for env. Use jq directly to
# keep operator precedence explicit.
export TESSIE_FIELDS="$(jq -r '(.fields // []) | join(",")' /data/options.json)"

if [ -z "$TESSIE_TOKEN" ]; then
  bashio::log.fatal "tessie_token is empty. Open the add-on Configuration tab and paste your token."
  exit 2
fi
if [ -z "$TESSIE_VIN" ]; then
  bashio::log.fatal "tessie_vin is empty. Enter your 17-char VIN in the Configuration tab."
  exit 2
fi
if [ -z "$TESSIE_FIELDS" ]; then
  bashio::log.fatal "fields list is empty. Pick at least one field in the Configuration tab."
  exit 2
fi

bashio::log.info "Selected fields: ${TESSIE_FIELDS}"

# --- MQTT credentials, auto-injected by Supervisor (services: mqtt:need) ------
export MQTT_HOST="$(bashio::services 'mqtt' 'host')"
export MQTT_PORT="$(bashio::services 'mqtt' 'port')"
export MQTT_USERNAME="$(bashio::services 'mqtt' 'username')"
export MQTT_PASSWORD="$(bashio::services 'mqtt' 'password')"
bashio::log.info "MQTT broker: ${MQTT_HOST}:${MQTT_PORT} (user=${MQTT_USERNAME})"

# --- Home zone resolution: manual override → zone.home auto-detect → none -----
USER_HOME_LAT="$(bashio::config 'home_latitude')"
USER_HOME_LON="$(bashio::config 'home_longitude')"

# Normalize to a numeric form so 0, 0.0, 0.00, "" and "null" all collapse to "0".
# bashio::config returns floats as "0.0" for the default — the previous string
# comparison against "0" missed that and incorrectly used 0.0 as a real override.
USER_HOME_LAT_N="$(awk -v v="$USER_HOME_LAT" 'BEGIN { printf "%g", v + 0 }')"
USER_HOME_LON_N="$(awk -v v="$USER_HOME_LON" 'BEGIN { printf "%g", v + 0 }')"

if [ "$USER_HOME_LAT_N" != "0" ] && [ "$USER_HOME_LON_N" != "0" ]; then
  export HOME_LATITUDE="$USER_HOME_LAT"
  export HOME_LONGITUDE="$USER_HOME_LON"
  bashio::log.info "Home zone (manual override from add-on options): lat=${HOME_LATITUDE} lon=${HOME_LONGITUDE} radius=${HOME_RADIUS_METERS}m"
else
  HOME_JSON="$(curl -fsSL \
    -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
    -H "Content-Type: application/json" \
    http://supervisor/core/api/states/zone.home || true)"

  if [ -n "$HOME_JSON" ] && echo "$HOME_JSON" | jq -e '.attributes.latitude' >/dev/null 2>&1; then
    export HOME_LATITUDE="$(echo "$HOME_JSON" | jq -r '.attributes.latitude')"
    export HOME_LONGITUDE="$(echo "$HOME_JSON" | jq -r '.attributes.longitude')"
    HA_RADIUS="$(echo "$HOME_JSON" | jq -r '.attributes.radius // empty')"
    if [ "$HOME_RADIUS_METERS" = "100" ] && [ -n "$HA_RADIUS" ]; then
      export HOME_RADIUS_METERS="$HA_RADIUS"
    fi
    bashio::log.info "Home zone (auto-detected from zone.home): lat=${HOME_LATITUDE} lon=${HOME_LONGITUDE} radius=${HOME_RADIUS_METERS}m"
  else
    bashio::log.warning "Could not fetch zone.home from HA, and no manual override set — home/not_home state will not be computed."
    export HOME_LATITUDE=0
    export HOME_LONGITUDE=0
  fi
fi

bashio::log.info "Tessie endpoint: ${TESSIE_URL}"
bashio::log.info "Starting Tessie MQTT bridge for VIN ${TESSIE_VIN}"
exec python3 /app/bridge.py
