#!/usr/bin/env python3
"""GET https://127.0.0.1:8080/api/health (ignore self-signed cert)."""
from __future__ import annotations

import ssl
import urllib.error
import urllib.request


def main() -> int:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request("https://127.0.0.1:8080/api/health")
    try:
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            snippet = body[:200]
            print(f"HTTP {resp.status} {snippet}")
            return 0 if 200 <= int(resp.status) < 300 else 1
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} {e.reason}")
        return 1
    except Exception as e:
        print(str(e))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
