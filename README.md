# OpenVPN Bridge for Chrome

Route **only Chrome's traffic** through your OpenVPN server — everything
else on your Mac (SSH, terminals, other apps) stays on your normal
internet connection.

**The problem this solves:** a normal OpenVPN client (OpenVPN Connect,
Tunnelblick) tunnels your *entire machine*. If you only need the VPN for
a few websites, that's overkill — and it often breaks things like SSH
sessions, local dev tools, or video calls. This project gives Chrome its
own private path into the VPN and leaves the rest of the system alone.

> **Platform:** macOS (Apple Silicon or Intel). Chrome or any Chromium
> browser for the extension.

## How it works

A Chrome extension alone cannot speak the OpenVPN protocol — extensions
can only point the browser at a proxy. So this project has two halves:

```
Chrome ──(extension sets SOCKS5 proxy)──▶ 127.0.0.1:1080 (gost proxy)
                                              │ outbound bound to the
                                              │ VPN's tun interface
                                              ▼
                                        OpenVPN tunnel ──▶ your VPN server
```

- **`vpn-bridge.sh`** starts OpenVPN with `--route-nopull`, so the
  tunnel comes up **without touching the system routing table** — no
  other app even knows it exists. It then starts [gost](https://github.com/go-gost/gost),
  a small SOCKS5 proxy on `127.0.0.1:1080` whose outbound connections
  are pinned to the VPN's tun interface.
- **`extension/`** is a Chrome extension (Manifest V3) that toggles
  Chrome between "direct" and that local proxy — for all traffic, or
  only for a list of domains you choose.

DNS lookups for proxied traffic are resolved over encrypted
DNS-over-HTTPS (Cloudflare `1.1.1.1`), so ISP-level DNS blocking can't
interfere and your ISP's resolver never sees them.

## Beginner's guide

### What you need

- A Mac with [Homebrew](https://brew.sh) installed
- Google Chrome (or Brave/Edge/another Chromium browser)
- An OpenVPN profile — a `.ovpn` file from your VPN provider or your
  own OpenVPN server

### Step 1 — Get the code and tools

```sh
git clone https://github.com/deshabhishek007/chrome-openvpn.git
cd chrome-openvpn
brew install openvpn gost
```

### Step 2 — Add your VPN profile

Copy your `.ovpn` file(s) into the `profiles/` folder:

```sh
cp ~/Downloads/my-server.ovpn profiles/
```

If your VPN needs a username and password, create `profiles/auth.txt`
with the username on line 1 and the password on line 2.

> **Tip:** if your profile is already imported into the OpenVPN Connect
> app and you can't find the original file, the app keeps copies in
> `~/Library/Application Support/OpenVPN Connect/profiles/` — you can
> copy them from there and rename them to something memorable.
>
> The `profiles/` folder is listed in `.gitignore`, so your VPN
> credentials can never be accidentally committed or pushed.

### Step 3 — Load the extension in Chrome

1. Open `chrome://extensions` in Chrome
2. Turn on **Developer mode** (toggle in the top-right corner)
3. Click **Load unpacked** and select this project's `extension/` folder

### Step 4 — Start the bridge

```sh
./vpn-bridge.sh
```

- It asks for your Mac password once (creating a network tunnel
  requires admin rights).
- With one profile it connects straight away; with several it shows a
  numbered menu. You can also pass a name: `./vpn-bridge.sh eu` matches
  `my-vpn-eu.ovpn`.
- Leave the terminal window running. Ctrl-C disconnects everything.

### Step 5 — Turn it on in Chrome

Click the **OpenVPN Bridge** icon in Chrome's toolbar:

- **All Chrome traffic** — every site you open in Chrome goes through
  the VPN.
- **Only these sites** — list just the domains that need the VPN (one
  per line, subdomains included); everything else stays direct and
  fast.

Hit **Connect** — the popup shows Chrome's public IP so you can confirm
it changed. Meanwhile, `ssh`, `curl`, and every other app on your Mac
still use your normal connection. That's the whole point!

## Optional: control everything from the popup

Instead of keeping a terminal open, the popup can start and stop the
tunnel itself — pick a profile from the dropdown, press **Start**, and
the proxy toggle flips on automatically. One-time setup (after loading
the extension):

```sh
./host/install.sh
```

This wires up three things:

1. A **native messaging host** manifest, so Chrome is allowed to launch
   `host/vpn-bridge-host.py` — this is Chrome's sanctioned mechanism
   for extensions to talk to local programs, locked to this extension's
   ID.
2. A **root helper** at `/usr/local/libexec/vpn-bridge-helper`
   (root-owned, read-only) that does only the privileged parts:
   start openvpn with `--route-nopull` + `--script-security 1`,
   add/remove the interface-scoped route, stop the tunnel. Profiles are
   referenced by name only and resolved inside this project's
   `profiles/` folder.
3. A **sudoers rule** (`/etc/sudoers.d/vpn-bridge`) allowing your user
   to run exactly that one helper without a password — nothing else
   gains passwordless sudo.

Then reload the extension once (`chrome://extensions` → reload icon).

To undo it all:

```sh
sudo rm /etc/sudoers.d/vpn-bridge /usr/local/libexec/vpn-bridge-helper
rm ~/Library/Application\ Support/Google/Chrome/NativeMessagingHosts/com.vpnbridge.host.json
```

The terminal script keeps working either way; both control the same
tunnel and proxy.

## Troubleshooting

**The popup says "Could not reach the internet through the proxy."**
The bridge isn't running (or died). Start `./vpn-bridge.sh`, or press
Start in the popup if you installed the optional control.

**`bind: address already in use` on startup.**
Something else holds port 1080. The script cleans up its own leftovers
automatically; if another app owns the port, run on a different one:
`PORT=1081 ./vpn-bridge.sh` and change the port in the popup to match.

**The tunnel comes up but sites time out.**
Check `/tmp/openvpn-bridge.log`. If the log looks healthy, your VPN
server may not allow forwarding to the destination. Test with:
`curl --socks5-hostname 127.0.0.1:1080 https://api.ipify.org` — it
should print the VPN server's IP.

**"tunnel did not come up in 45s".**
Usually wrong credentials (check `profiles/auth.txt`), a blocked UDP
port, or the server being down. The last log lines are printed with the
error.

**I use a full VPN app too (OpenVPN Connect, Tunnelblick, ...).**
Keep it disconnected while the bridge runs. If it connects, it captures
the whole system — including Chrome — and the "only Chrome" behavior is
gone until you disconnect it.

## ISP proxy profiles (no VPN needed)

For plain HTTP/SOCKS5 upstream proxies (ISP/static-residential products) a
real OpenVPN tunnel is unnecessary — `.ovpn`'s `remote` must be an actual
OpenVPN server, and `http-proxy`/`socks-proxy` directives only relay the
*tunnel transport*, they don't change the exit IP. To exit **from** the
proxy's IP, run the local chain instead:

```sh
python3 gen-isp-profiles.py      # writes profiles/*.proxy from ~/Downloads/账号信息 exports
./proxy-bridge.sh isp-evomi      # or pick from the same menu as vpn-bridge
```

`proxy-bridge.sh` serves the same `127.0.0.1:1080` SOCKS5 the extension
already uses — gost chains `socks5 → upstream http/socks5 proxy`, so no
tun, no sudo, and the exit IP is the ISP endpoint itself. Profile files
live in gitignored `profiles/`:

```
LABEL=…   UPSTREAM=http://user:pass@host:port   COUNTRY=US
```

If you ever need a *literal* `.ovpn` that exits via an ISP IP (e.g. OpenVPN
Connect on a phone, or non-browser apps), the ISP proxy must sit on the
server side: run one `openvpn` server instance per product on a VPS with
its egress chained into that product's proxy (tun2socks/gost). Nothing
client-side can make a proxy look like an OpenVPN endpoint.

## Notes & limitations

- macOS only. The interface-scoped routing trick (`route -ifscope`) is
  macOS-specific; a Linux port would use network namespaces instead.
- The proxy setting applies to the whole Chrome profile (all tabs and
  windows), not other browsers or apps.
- `localhost` is always bypassed, so local dev servers keep working.
- Keep `.ovpn` files and `auth.txt` private; they contain credentials.

## License

[MIT](LICENSE)
