#!/bin/sh
# Build the real simulator app on a Mac; signing and device installation are separate.
set -eu
if [ "$(uname -s)" != "Darwin" ]; then
    printf '%s\n' 'Для сборки приложения iPhone требуется macOS с Xcode.' >&2
    exit 1
fi
ORBIT_PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ORBIT_PROJECT_DIR"
xcrun --find swift >/dev/null
xcrun --find xcodebuild >/dev/null
swift test
xcodebuild -project AgentOrbit.xcodeproj -scheme Orbit \
    -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' \
    -configuration Debug -derivedDataPath build CODE_SIGNING_ALLOWED=NO build
printf '%s\n' 'Сборка для симулятора завершена. Для установки на iPhone выберите свою Team в Xcode.'
