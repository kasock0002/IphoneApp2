#!/bin/sh
# Compile and test the Foundation core on Linux. This does not build the iPhone UI.
# Prerequisite: explicitly pull swift:6.0.3-jammy (or ORBIT_SWIFT_IMAGE) first.
set -eu

ORBIT_PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ORBIT_SWIFT_IMAGE=${ORBIT_SWIFT_IMAGE:-swift:6.0.3-jammy}
set --
if [ -n "${ORBIT_DOCKER_HOST:-}" ]; then
    set -- --host "$ORBIT_DOCKER_HOST"
fi

docker "$@" run --rm --pull=never \
    --user "$(id -u):$(id -g)" --network none \
    --env CLANG_MODULE_CACHE_PATH=/tmp/orbit-clang \
    --env SWIFT_MODULECACHE_PATH=/tmp/orbit-swift \
    --env SWIFTPM_MODULECACHE_OVERRIDE=/tmp/orbit-swiftpm \
    --mount "type=bind,source=$ORBIT_PROJECT_DIR,target=/app,readonly" \
    --workdir /app "$ORBIT_SWIFT_IMAGE" sh -eu -c '
        swift test --scratch-path /tmp/orbit-build \
            --cache-path /tmp/orbit-cache --config-path /tmp/orbit-config \
            --security-path /tmp/orbit-security
        swiftc -frontend -parse AgentOrbit/*.swift
        printf "%s\n" "Native Swift syntax parse passed. iPhone UI build still requires Xcode."
    '
