#!/usr/bin/env python3
"""Generate profiles/*.proxy files from the local ISP account exports in
~/Downloads/账号信息/ (same source the egress-control catalog was seeded
from — the catalog never returns plaintext credentials, so we parse the
original exports).

Sources:
  evomi.isp-proxies.txt        host:port:user:pass   (pass embeds exit IP)
  isp-proxies-premium-http.txt host:port:user:pass   (CatProxies, 1 IP)
  isp-proxies-export.csv       Geonode shared ISP (Username/Password cols)
  Webshare 20 proxies.txt      ip:port:user:pass     (Webshare account 2 —
                               all 20 IPs match webshare2_isp_* endpoints)

Webshare account-1 products (webshare_isp_*) have no local export — skipped
with a warning. Re-run after dropping more exports into the folder.
"""
import csv
import re
import sys
from pathlib import Path

SRC = Path.home() / "Downloads" / "账号信息"
OUT = Path(__file__).resolve().parent / "profiles"

GEONODE_LABEL = "Geonode 共享 ISP US"
CATPROXIES_LABEL = "CatProxies Premium USA Retail ISP"
EVOMI_LABEL = "Evomi 静态 ISP US"
WEBSHARE_LABEL = "Webshare 静态住宅 ISP（账号2）"

# catalog productKey by country for webshare account-2 (from control plane)
WEBSHARE_CC_KEY = {
    "CA": "webshare2_isp_ca", "GB": "webshare2_isp_gb", "US": "webshare2_isp_us",
}

# Webshare export has no country column; resolved from catalog endpoints.
# ip -> country, from GET /v1/proxy-products/<id>/endpoints (2026-09-28).
WEBSHARE_IP_CC = {
    "82.23.103.158": "CA", "82.23.103.56": "CA", "82.23.96.145": "CA",
    "82.23.96.148": "CA", "82.23.96.154": "CA", "82.23.96.69": "CA",
    "104.252.62.128": "GB", "104.252.81.164": "GB", "104.252.81.99": "GB",
    "104.252.97.17": "GB", "195.40.128.220": "GB", "195.40.128.43": "GB",
    "195.40.142.178": "GB", "87.86.25.245": "GB",
    "104.243.210.18": "US", "138.226.77.205": "US", "138.226.77.21": "US",
    "192.46.189.91": "US", "192.46.200.114": "US", "9.142.35.100": "US",
}


def emit(fname: str, label: str, upstream: str, country: str = "") -> None:
    body = f"LABEL={label}\nUPSTREAM={upstream}\n"
    if country:
        body += f"COUNTRY={country}\n"
    path = OUT / fname
    path.write_text(body)
    print(f"  {fname}")


def colon4(path: Path):
    """Yield (host, port, user, pw) from host:port:user:pass lines."""
    for line in path.read_text().splitlines():
        parts = line.strip().split(":")
        if len(parts) >= 4 and parts[0] and parts[1].isdigit():
            yield parts[0], parts[1], parts[2], ":".join(parts[3:])


def main() -> int:
    OUT.mkdir(exist_ok=True)
    n = 0

    f = SRC / "evomi.isp-proxies.txt"
    if f.exists():
        for host, port, user, pw in colon4(f):
            m = re.match(r"(\d+\.\d+\.\d+\.\d+)_", pw)
            ip = m.group(1) if m else "unknown"
            emit(f"isp-evomi-{ip}.proxy", f"{EVOMI_LABEL} {ip}",
                 f"http://{user}:{pw}@{host}:{port}", "US")
            n += 1
    else:
        print(f"! missing {f}")

    f = SRC / "isp-proxies-premium-http.txt"
    if f.exists():
        for host, port, user, pw in colon4(f):
            emit(f"isp-catproxies-{host}.proxy", f"{CATPROXIES_LABEL} {host}",
                 f"http://{user}:{pw}@{host}:{port}", "US")
            n += 1
    else:
        print(f"! missing {f}")

    f = SRC / "isp-proxies-export.csv"
    if f.exists():
        with f.open() as fh:
            for row in csv.DictReader(fh):
                ip = (row.get("IP") or "").strip()
                user = (row.get("Username") or "").strip()
                pw = (row.get("Password") or "").strip()
                port = (row.get("HTTP Port") or "61232").strip()
                cc = (row.get("Country Code") or "US").strip().upper()
                if ip and user:
                    emit(f"isp-geonode-{ip}.proxy", f"{GEONODE_LABEL} {ip}",
                         f"http://{user}:{pw}@{ip}:{port}", cc)
                    n += 1
    else:
        print(f"! missing {f}")

    f = SRC / "Webshare 20 proxies.txt"
    if f.exists():
        for host, port, user, pw in colon4(f):
            cc = WEBSHARE_IP_CC.get(host, "")
            key = WEBSHARE_CC_KEY.get(cc, "webshare_isp")
            emit(f"isp-{key}-{host}.proxy", f"{WEBSHARE_LABEL} {cc} {host}",
                 f"http://{user}:{pw}@{host}:{port}", cc)
            n += 1
    else:
        print(f"! missing {f}")

    print(f"\n{n} profiles written to {OUT}")
    print("note: webshare_isp_* (account 1) has no local export — skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
