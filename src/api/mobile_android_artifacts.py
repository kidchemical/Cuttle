"""APK identity and signature validation for Android update publication."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile

PACKAGE_NAME = "com.cuttle.mobile"
MAX_APK_BYTES = 256 * 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def baked_hash(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path) as archive:
            entry = archive.getinfo("assets/cuttle-mobile-build.json")
            if entry.file_size > 16 * 1024:
                return None
            data = json.loads(archive.read(entry))
        value = data.get("hash") if isinstance(data, dict) else None
        return value if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{20}", value) else None
    except (OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile):
        return None


def inspect_apk(path: Path) -> dict:
    """Verify with Android SDK tools; never infer signing from loose build files."""
    if not 0 < path.stat().st_size <= MAX_APK_BYTES:
        raise ValueError("APK size is outside the supported range")
    identity = baked_hash(path)
    if not identity:
        raise ValueError("APK is missing a valid Cuttle build identity")
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if not sdk:
        raise ValueError("Publishing requires Android SDK Build Tools: set ANDROID_HOME")
    tools = Path(sdk) / "build-tools"
    suffix = ".exe" if os.name == "nt" else ""
    candidates = sorted(
        (p for p in tools.glob("*") if (p / "lib/apksigner.jar").is_file()
         and (p / ("aapt" + suffix)).is_file()),
        key=lambda p: tuple(int(n) for n in re.findall(r"\d+", p.name)), reverse=True,
    )
    if not candidates:
        raise ValueError("Android SDK Build Tools with apksigner and aapt are required to publish")
    tools = candidates[0]
    java_home = os.environ.get("JAVA_HOME")
    java = str(Path(java_home) / "bin" / ("java" + suffix)) if java_home else "java"
    signed = subprocess.run(
        [java, "-jar", str(tools / "lib/apksigner.jar"), "verify", "--print-certs", str(path)],
        capture_output=True, text=True, check=True, timeout=60,
    )
    certificates = re.findall(r"Signer #\d+ certificate SHA-256 digest: ([a-fA-F0-9]{64})", signed.stdout)
    if len(certificates) != 1:
        raise ValueError("Update APK must have exactly one verified signing certificate")
    badging = subprocess.run(
        [str(tools / ("aapt" + suffix)), "dump", "badging", str(path)],
        capture_output=True, text=True, check=True, timeout=30,
    ).stdout
    package = re.search(r"^package: name='([^']+)' versionCode='(\d+)' versionName='([^']*)'", badging, re.M)
    if not package or package[1] != PACKAGE_NAME:
        raise ValueError("APK package must be " + PACKAGE_NAME)
    version = int(package[2])
    if not 0 < version <= 2100000000:
        raise ValueError("APK versionCode is invalid")
    return {
        "hash": identity, "sha256": sha256_file(path), "size": path.stat().st_size,
        "packageName": package[1], "versionCode": version, "packageVersion": package[3],
        "signingCertSha256": certificates[0].lower(),
        "channel": "debug" if re.search(r"^application-debuggable", badging, re.M) else "release",
    }
