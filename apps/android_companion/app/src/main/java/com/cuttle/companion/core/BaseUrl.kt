package com.cuttle.companion.core

/**
 * Reject unusable Cuttle base URLs before anything is persisted or started.
 * Returns null when the URL is usable, else a user-facing reason.
 * Explicit ports (including 80/443 and custom 8001/8443/8890) are kept
 * as typed; an absent port is left absent for the platform default.
 * No scheme rewriting and no cleartext opt-in happen here.
 *
 * Pure JVM (only java.net.URI): compilable and runnable without Android.
 */
fun validateBaseUrl(raw: String): String? {
    val base = raw.trim().trimEnd('/')
    if (base.isEmpty()) return "Enter the Cuttle base URL, including scheme and port."
    val uri = try {
        java.net.URI(base)
    } catch (_: Exception) {
        return "That URL is not valid — check the scheme, host and port."
    }
    if (uri.scheme != "http" && uri.scheme != "https") {
        return "The URL must start with http:// or https://."
    }
    if (uri.host.isNullOrBlank()) {
        return "The URL must include a host."
    }
    // java.net.URI reports -1 when no port is present (platform default
    // applies) but does not bound explicit numeric ports, so enforce
    // 1–65535 here instead of trusting the parser.
    val port = uri.port
    if (port != -1 && (port < 1 || port > 65535)) {
        return "Enter a valid port 1–65535, or leave the port out."
    }
    return null
}
