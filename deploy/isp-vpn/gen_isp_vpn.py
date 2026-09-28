#!/usr/bin/env python3
"""Generate the ns515786 ISP-VPN deployment: PKI, docker-compose.yml and
profiles/*.ovpn for chrome-openvpn.

Each usable ISP exit (Evomi static / Geonode shared / CatProxies premium,
parsed from ~/Downloads/账号信息) becomes one container: openvpn server on
tcp/1194 inside, published as 100.96.0.6:PORT/tcp on the host mesh IP.
Server egress is chained into that exit's upstream proxy via gost `red`
(iptables REDIRECT inside the container), so the VPN exit IP == ISP IP.

Single container, N exits: the client certificate's CN picks the exit.
ccd/ pins each CN to a fixed tunnel IP (10.99.0.N); iptables redirects TCP
from that IP to a per-product gost `red` listener chained to the upstream.

Outputs (all gitignored / server-only where they contain credentials):
  deploy/isp-vpn/pki/            CA + server cert + one client cert per exit
  deploy/isp-vpn/ccd/            per-CN ifconfig-push files
  deploy/isp-vpn/routes.txt      tunnel_ip  local_port  upstream_url
  deploy/isp-vpn/docker-compose.yml   one ispvpn service on :12300/tcp
  deploy/isp-vpn/upstreams.json  name -> upstream/ip/port manifest
  profiles/isp-*.ovpn            client profiles, all remote :12300,
                                 differing only by embedded client cert

Usage: python3 gen_isp_vpn.py            # full generate
       python3 gen_isp_vpn.py --no-pki   # reuse existing pki/
"""
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

SRC = Path.home() / "Downloads" / "账号信息"
HERE = Path(__file__).resolve().parent
PKI = HERE / "pki"
PROFILES = HERE.parent.parent / "profiles"
REMOTE_HOST = "167.114.209.179"  # public IP bind on the VPS
MESH_BIND = "100.96.0.6"         # extra bind on CloudflareWARP (SSH-fwd target / fallback)
REMOTE_PORT = 12300
# Kane's LAN whitelists outbound ports (web/mail/ssh only), so profiles dial
# 127.0.0.1 and reach the server through a persistent SSH forward:
#   launchd com.kane.ispvpn-fwd -> ssh -N -L 127.0.0.1:12300:100.96.0.6:12300 ns515786
OVPN_REMOTE = "127.0.0.1"
GOST_PORT_BASE = 12400
CCD = HERE / "ccd"
ROUTES = HERE / "routes.txt"


def colon4(path: Path):
    for line in path.read_text().splitlines():
        parts = line.strip().split(":")
        if len(parts) >= 4 and parts[0] and parts[1].isdigit():
            yield parts[0], parts[1], parts[2], ":".join(parts[3:])


def collect_instances() -> list[dict]:
    out = []
    f = SRC / "evomi.isp-proxies.txt"
    for host, port, user, pw in colon4(f):
        m = re.match(r"(\d+\.\d+\.\d+\.\d+)_", pw)
        ip = m.group(1) if m else "x"
        out.append({"name": f"isp-evomi-{ip}", "country": "US", "exit_ip": ip,
                    "upstream": f"http://{user}:{pw}@{host}:{port}"})
    f = SRC / "isp-proxies-export.csv"
    with f.open() as fh:
        for row in csv.DictReader(fh):
            ip = (row.get("IP") or "").strip()
            user = (row.get("Username") or "").strip()
            pw = (row.get("Password") or "").strip()
            port = (row.get("HTTP Port") or "61232").strip()
            if ip and user:
                out.append({"name": f"isp-geonode-{ip}", "country": "US",
                            "exit_ip": ip,
                            "upstream": f"http://{user}:{pw}@{ip}:{port}"})
    f = SRC / "isp-proxies-premium-http.txt"
    for host, port, user, pw in colon4(f):
        out.append({"name": f"isp-catproxies-{host}", "country": "US",
                    "exit_ip": host,
                    "upstream": f"http://{user}:{pw}@{host}:{port}"})
    return out


def sh(*args: str) -> None:
    subprocess.run(args, check=True)


def sign_cert(cn: str, usage: str, out_prefix: str) -> None:
    key, crt = PKI / f"{out_prefix}.key", PKI / f"{out_prefix}.crt"
    if crt.exists():
        return
    sh("openssl", "ecparam", "-name", "prime256v1", "-genkey", "-out",
       str(key))
    sh("openssl", "req", "-new", "-key", str(key), "-subj", f"/CN={cn}",
       "-out", str(PKI / f"{out_prefix}.csr"))
    ext = PKI / "ext.cnf"
    ext.write_text(
        "extendedKeyUsage=" + usage + "\nkeyUsage=digitalSignature" +
        (",keyEncipherment\n" if usage == "serverAuth" else "\n"))
    sh("openssl", "x509", "-req", "-in", str(PKI / f"{out_prefix}.csr"),
       "-CA", str(PKI / "ca.crt"), "-CAkey", str(PKI / "ca.key"),
       "-CAcreateserial", "-days", "3650", "-extfile", str(ext),
       "-out", str(crt))


def gen_pki(instances: list[dict]) -> None:
    PKI.mkdir(parents=True, exist_ok=True)
    if not (PKI / "ca.crt").exists():
        sh("openssl", "ecparam", "-name", "prime256v1", "-genkey", "-out",
           str(PKI / "ca.key"))
        sh("openssl", "req", "-x509", "-new", "-SHA256", "-key",
           str(PKI / "ca.key"), "-subj", "/CN=isp-vpn-ca", "-days", "3650",
           "-out", str(PKI / "ca.crt"))
    sign_cert("isp-vpn-server", "serverAuth", "server")
    for inst in instances:  # client cert per exit: CN selects the route
        sign_cert(inst["name"], "clientAuth", f"client-{inst['name']}")
    if not (PKI / "ta.key").exists():
        sh("/opt/homebrew/sbin/openvpn", "--genkey", "secret",
           str(PKI / "ta.key"))


def emit_compose(instances: list[dict]) -> None:
    CCD.mkdir(exist_ok=True)
    route_lines = []
    for i, inst in enumerate(instances, start=2):
        inst["tun_ip"] = f"10.99.0.{i}"
        inst["gost_port"] = GOST_PORT_BASE + i
        inst["port"] = REMOTE_PORT
        (CCD / inst["name"]).write_text(
            f"ifconfig-push {inst['tun_ip']} 255.255.255.0\n")
        route_lines.append(
            f"{inst['tun_ip']} {inst['gost_port']} {inst['upstream']}")
    ROUTES.write_text("\n".join(route_lines) + "\n")

    compose = f"""services:
  ispvpn:
    image: ispvpn:latest
    build: .
    container_name: ispvpn
    restart: unless-stopped
    cap_add: [NET_ADMIN]
    devices: ['/dev/net/tun']
    ports:
      - '{REMOTE_HOST}:{REMOTE_PORT}:1194/tcp'
      - '{MESH_BIND}:{REMOTE_PORT}:1194/tcp'
    volumes:
      - './pki:/pki:ro'
      - './ccd:/etc/ispvpn/ccd:ro'
      - './routes.txt:/etc/ispvpn/routes.txt:ro'

# ns515786's default docker pools are exhausted; pin an unused subnet.
networks:
  default:
    ipam:
      config:
        - subnet: 10.204.0.0/24
"""
    (HERE / "docker-compose.yml").write_text(compose)


OVPN_TMPL = """# {name} — exit via {exit_ip} ({label})
client
dev tun
proto tcp-client
remote {host} {port}
# The real uplink is the local SSH forward to {REMOTE_HOST}: the route below
# keeps that SSH session on the physical gateway once redirect-gateway lands,
# and the WARP/kane.ovpn transports stay exempt so the mesh keeps working.
route {REMOTE_HOST} 255.255.255.255 net_gateway
route 162.158.0.0 255.254.0.0 net_gateway
route 198.41.128.0 255.255.128.0 net_gateway
route 1.1.1.1 255.255.255.255 net_gateway
route 1.0.0.1 255.255.255.255 net_gateway
route 34.92.0.244 255.255.255.255 net_gateway
resolv-retry infinite
nobind
persist-key
persist-tun
remote-cert-tls server
verify-x509-name isp-vpn-server name
auth SHA256
cipher AES-256-GCM
auth-nocache
key-direction 1
mssfix 1380
verb 3
<ca>
{ca}</ca>
<cert>
{cert}</cert>
<key>
{key}</key>
<tls-crypt>
{ta}</tls-crypt>
"""


def emit_ovpn(instances: list[dict]) -> None:
    ca = (PKI / "ca.crt").read_text().strip() + "\n"
    ta = (PKI / "ta.key").read_text().strip() + "\n"
    PROFILES.mkdir(exist_ok=True)
    for inst in instances:
        cert = (PKI / f"client-{inst['name']}.crt").read_text().strip() + "\n"
        key = (PKI / f"client-{inst['name']}.key").read_text().strip() + "\n"
        body = OVPN_TMPL.format(name=inst["name"], exit_ip=inst["exit_ip"],
                                label=inst["name"], host=OVPN_REMOTE,
                                port=inst["port"], ca=ca, cert=cert,
                                key=key, ta=ta, REMOTE_HOST=REMOTE_HOST)
        (PROFILES / f"{inst['name']}.ovpn").write_text(body)


def main() -> int:
    instances = collect_instances()
    if not instances:
        print("no upstreams parsed from", SRC)
        return 1
    gen_pki(instances)
    emit_compose(instances)
    emit_ovpn(instances)
    (HERE / "upstreams.json").write_text(json.dumps(
        {i["name"]: {"tun_ip": i["tun_ip"], "gost_port": i["gost_port"],
                     "exit_ip": i["exit_ip"], "country": i["country"],
                     "upstream": i["upstream"]} for i in instances},
        indent=1))
    print(f"{len(instances)} exits -> one service on "
          f"{REMOTE_HOST}:{REMOTE_PORT}/tcp")
    print("compose:", HERE / "docker-compose.yml")
    print("ovpn:", PROFILES, f"({len(instances)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
