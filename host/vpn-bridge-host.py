#!/usr/bin/env python3
"""Native messaging host for the OpenVPN Bridge Chrome extension.

Chrome launches this process and exchanges length-prefixed JSON messages
over stdin/stdout. It runs as the regular user: it manages the gost
SOCKS5 proxy itself and delegates the root-only work (openvpn, scoped
route) to /usr/local/libexec/vpn-bridge-helper via passwordless sudo,
which host/install.sh sets up.

Commands: {"cmd": "list" | "status" | "start", "profile": ...} | {"cmd": "stop"}
"""

import glob
import json
import os
import re
import struct
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE_DIR = os.path.join(BASE, "profiles")
HELPER = "/usr/local/libexec/vpn-bridge-helper"
PORT = int(os.environ.get("VPN_BRIDGE_PORT", "1080"))
GOST_CFG = "/tmp/gost-bridge.yml"
STATE = "/tmp/vpn-bridge-state.json"
GOST_CANDIDATES = ["/opt/homebrew/bin/gost", "/usr/local/bin/gost"]

NOT_INSTALLED = (
    "Bridge control is not installed. Run host/install.sh once "
    "from the project folder (it will ask for your password)."
)


def read_msg():
    raw = sys.stdin.buffer.read(4)
    if len(raw) < 4:
        sys.exit(0)
    (n,) = struct.unpack("<I", raw)
    return json.loads(sys.stdin.buffer.read(n))


def send(obj):
    data = json.dumps(obj).encode()
    sys.stdout.buffer.write(struct.pack("<I", len(data)) + data)
    sys.stdout.buffer.flush()


def gost_bin():
    for p in GOST_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def gost_pid():
    r = run(["lsof", "-ti", f"tcp:{PORT}", "-sTCP:LISTEN"])
    pids = r.stdout.split()
    return int(pids[0]) if pids else None


def openvpn_running():
    return bool(run(["pgrep", "-f", "--", "--route-nopull"]).stdout.strip())


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    with open(STATE, "w") as f:
        json.dump(state, f)


def kill_gost():
    pid = gost_pid()
    if pid:
        for sig, wait in ((15, 1.0), (9, 0.5)):
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                return
            time.sleep(wait)
            if gost_pid() != pid:
                return


def cmd_list():
    profiles = []
    for f in sorted(glob.glob(os.path.join(PROFILE_DIR, "*.ovpn"))):
        server = ""
        try:
            with open(f) as fh:
                text = fh.read()
                # Prefer the profile's "exit via <ip>" banner (the real
                # upstream exit) over `remote`, which after the SSH-fwd
                # rework is always the loopback 127.0.0.1.
                m = re.search(r"^#\s*\S+\s*[—-]\s*exit\s+via\s+(\S+)", text, re.M)
                if m:
                    server = m.group(1)
                else:
                    m = re.search(r"^remote\s+(\S+)", text, re.M)
                    server = m.group(1) if m else ""
        except OSError:
            pass
        profiles.append({"name": os.path.basename(f)[:-5], "server": server})
    return {"ok": True, "profiles": profiles}


def cmd_status():
    state = load_state()
    tunnel = openvpn_running()
    proxy = gost_pid() is not None
    return {
        "ok": True,
        "running": tunnel and proxy,
        "tunnel": tunnel,
        "proxy": proxy,
        "iface": state.get("iface", ""),
        "profile": state.get("profile", "") if tunnel else "",
        "port": PORT,
    }


def start_gost(iface):
    gost = gost_bin()
    if not gost:
        return "gost not found. Install it with: brew install gost"
    with open(GOST_CFG, "w") as f:
        f.write(f"""services:
- name: vpn-socks
  addr: 127.0.0.1:{PORT}
  handler:
    type: socks5
  listener:
    type: tcp
  interface: {iface}
  resolver: doh
resolvers:
- name: doh
  nameservers:
  - addr: https://1.1.1.1/dns-query
    prefer: ipv4
""")
    with open(os.devnull, "wb") as devnull:
        subprocess.Popen(
            [gost, "-C", GOST_CFG],
            stdout=devnull, stderr=devnull,
            start_new_session=True,  # survives this host process exiting
        )
    for _ in range(20):
        if gost_pid():
            return None
        time.sleep(0.25)
    return "proxy failed to start on port %d" % PORT


def cmd_start(profile):
    if not re.fullmatch(r"[A-Za-z0-9._-]+", profile or ""):
        return {"ok": False, "error": "invalid profile name"}
    kill_gost()
    r = run(["sudo", "-n", HELPER, "start", profile], start_new_session=True)
    if r.returncode != 0:
        err = (r.stderr or "").strip()
        if "password is required" in err or "command not found" in err or not os.path.exists(HELPER):
            return {"ok": False, "error": NOT_INSTALLED, "notInstalled": True}
        return {"ok": False, "error": err[-400:] or "tunnel failed to start"}
    iface = r.stdout.strip().splitlines()[-1]
    err = start_gost(iface)
    if err:
        run(["sudo", "-n", HELPER, "stop"])
        return {"ok": False, "error": err}
    save_state({"profile": profile, "iface": iface})
    return {"ok": True, "iface": iface, "profile": profile, "port": PORT}


def cmd_stop():
    kill_gost()
    r = run(["sudo", "-n", HELPER, "stop"], start_new_session=True)
    if r.returncode != 0:
        err = (r.stderr or "").strip()
        if "password is required" in err or not os.path.exists(HELPER):
            return {"ok": False, "error": NOT_INSTALLED, "notInstalled": True}
        return {"ok": False, "error": err[-400:]}
    save_state({})
    return {"ok": True}


def handle(msg):
    cmd = msg.get("cmd")
    if cmd == "list":
        return cmd_list()
    if cmd == "status":
        return cmd_status()
    if cmd == "start":
        return cmd_start(msg.get("profile", ""))
    if cmd == "stop":
        return cmd_stop()
    return {"ok": False, "error": f"unknown command: {cmd}"}


def main():
    while True:
        try:
            send(handle(read_msg()))
        except SystemExit:
            raise
        except Exception as e:  # never leave Chrome hanging without a reply
            send({"ok": False, "error": f"host error: {e}"})


if __name__ == "__main__":
    main()
