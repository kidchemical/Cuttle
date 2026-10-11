#!/usr/bin/env bash
# Cuttle Mobile — Android build environment.
#
# Java / Android SDK / Gradle caches are USER data and live in the Cuttle home,
# never in this repository (a release install is read-only). Override the root
# with CUTTLE_ANDROID_TOOLS if your toolchain lives elsewhere.
#
#   source apps/mobile/android/android-env.sh
#   cd apps/mobile/android && ./gradlew assembleDebug

CUTTLE_HOME_DIR="${CUTTLE_HOME:-$HOME/.local/share/cuttle}"
CUTTLE_ANDROID_TOOLS="${CUTTLE_ANDROID_TOOLS:-$CUTTLE_HOME_DIR/toolchains/android}"
export JAVA_HOME="${JAVA_HOME:-$CUTTLE_ANDROID_TOOLS/jdk}"
export ANDROID_HOME="${ANDROID_HOME:-$CUTTLE_ANDROID_TOOLS/sdk}"
export ANDROID_USER_HOME="${ANDROID_USER_HOME:-$CUTTLE_ANDROID_TOOLS/user-home/.android}"
export GRADLE_USER_HOME="${GRADLE_USER_HOME:-$CUTTLE_ANDROID_TOOLS/gradle-cache}"
export JAVA_TOOL_OPTIONS="${JAVA_TOOL_OPTIONS:-} -Duser.home=$CUTTLE_ANDROID_TOOLS/user-home"
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$PATH"
