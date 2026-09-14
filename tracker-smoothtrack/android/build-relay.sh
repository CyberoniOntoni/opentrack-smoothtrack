#!/usr/bin/env bash
# Script to cross-compile relay.c for Android using the Android NDK
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

if [[ -z "${ANDROID_NDK_ROOT:-}" && -n "${ANDROID_NDK_HOME:-}" ]]; then
    ANDROID_NDK_ROOT="${ANDROID_NDK_HOME}"
fi

if [[ -z "${ANDROID_NDK_ROOT:-}" ]]; then
    echo "ANDROID_NDK_ROOT is not set. Looking for NDK in standard locations..."
    for candidate in \
        "$HOME/AppData/Local/Android/Sdk/ndk/"* \
        "/c/Program Files (x86)/Android/android-sdk/ndk/"* \
        "$HOME/Android/Sdk/ndk/"*; do
        if [[ -d "${candidate}" ]]; then
            ANDROID_NDK_ROOT="${candidate}"
            break
        fi
    done
fi

if [[ -n "${ANDROID_NDK_ROOT:-}" && -d "${ANDROID_NDK_ROOT}" ]]; then
    echo "Found Android NDK at: ${ANDROID_NDK_ROOT}"
    HOST_OS="linux-x86_64"
    if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" || "$OSTYPE" == "win32" ]]; then
        HOST_OS="windows-x86_64"
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        HOST_OS="darwin-x86_64"
    fi

    TOOLCHAIN="${ANDROID_NDK_ROOT}/toolchains/llvm/prebuilt/${HOST_OS}/bin"
    API_LEVEL=24

    # Build arm64-v8a (64-bit ARM - standard for all modern Android phones)
    echo "Compiling st-relay-arm64..."
    "${TOOLCHAIN}/aarch64-linux-android${API_LEVEL}-clang" \
        -O2 -Wall -Wextra -static relay.c -o st-relay-arm64
    chmod +x st-relay-arm64

    # Build armeabi-v7a (32-bit ARM - for older Android devices)
    echo "Compiling st-relay-armv7..."
    "${TOOLCHAIN}/armv7a-linux-androideabi${API_LEVEL}-clang" \
        -O2 -Wall -Wextra -static relay.c -o st-relay-armv7
    chmod +x st-relay-armv7

    echo "Relay binaries successfully built!"
    ls -la st-relay-arm64 st-relay-armv7
else
    echo "Android NDK not found. To compile manually:"
    echo "  \$ANDROID_NDK_ROOT/toolchains/llvm/prebuilt/<host>/bin/aarch64-linux-android24-clang -O2 -static relay.c -o st-relay-arm64"
    exit 1
fi
