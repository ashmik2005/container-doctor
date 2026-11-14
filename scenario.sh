#!/usr/bin/env bash
# Start / stop the broken-on-purpose scenarios.
#
#   ./scenario.sh list            show available scenarios
#   ./scenario.sh pull            pre-download all images (do this once)
#   ./scenario.sh up <name>       tear down whatever runs, start one scenario
#   ./scenario.sh down            remove every scenario container + network
#
# All scenarios share the compose project name "cdoc", so `down` can find and
# remove everything by label, whichever scenario was running.
set -euo pipefail
cd "$(dirname "$0")"
PROJECT=cdoc

down() {
    local ids
    ids=$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT")
    # -v also removes anonymous volumes (Postgres creates one per container).
    [ -n "$ids" ] && docker rm -fv $ids > /dev/null
    docker network ls -q --filter "label=com.docker.compose.project=$PROJECT" | xargs -r docker network rm > /dev/null 2>&1 || true
}

up() {
    local name="$1" dir="scenarios/$1"
    [ -f "$dir/docker-compose.yml" ] || { echo "No such scenario: $name"; exit 1; }
    down
    # Some scenarios fail inside `up` on purpose (port conflict, bad command),
    # so a non-zero exit here is expected and must not stop the script.
    docker compose -f "$dir/docker-compose.yml" -p "$PROJECT" up -d > /dev/null 2>&1 \
        || echo "(compose reported an error: expected for some scenarios)"
    # Give the failure time to happen (crash, restart loop, healthcheck...).
    local wait
    wait=$(grep -o '"wait_seconds": [0-9]*' "$dir/expected.json" | grep -o '[0-9]*$')
    sleep "${wait:-3}"
    docker ps -a --filter "label=com.docker.compose.project=$PROJECT" \
        --format "table {{.Names}}\t{{.Status}}"
}

case "${1:-}" in
    list) ls scenarios ;;
    pull) for d in scenarios/*/; do docker compose -f "$d/docker-compose.yml" pull -q; done ;;
    up)   up "${2:?usage: ./scenario.sh up <name>}" ;;
    down) down ;;
    *)    sed -n '2,8p' "$0"; exit 1 ;;
esac
