#!/bin/sh
# Single container, N ISP exits. Each client certificate CN is pinned to a
# fixed tunnel IP via ccd/; iptables redirects that source IP's TCP to the
# gost `red` listener chained to that product's upstream proxy.
set -e

GW=10.99.0.1
cat > /tmp/server.conf <<EOF
port 1194
proto udp
dev tun0
topology subnet
server 10.99.0.0 255.255.255.0
client-config-dir /etc/ispvpn/ccd
ca /pki/ca.crt
cert /pki/server.crt
key /pki/server.key
tls-crypt /pki/ta.key
dh none
cipher AES-256-GCM
auth SHA256
push "redirect-gateway def1 bypass-dhcp"
push "dhcp-option DNS $GW"
push "mssfix 1380"
keepalive 10 60
persist-key
persist-tun
verb 3
EOF

openvpn --config /tmp/server.conf --daemon --log-append /proc/1/fd/1
for i in $(seq 1 60); do
  ip link show tun0 >/dev/null 2>&1 && break
  sleep 0.5
done
ip link show tun0 >/dev/null 2>&1 || { echo "tun0 did not come up"; exit 1; }

# DNS for tunnel clients, resolved via the container's own egress (eth0).
dnsmasq --keep-in-foreground --no-resolv \
  --server=1.1.1.1 --server=8.8.8.8 \
  --listen-address="$GW" --port=53 --bind-interfaces \
  --log-facility=/proc/1/fd/1 &

iptables -t nat -A PREROUTING -i tun0 -p udp --dport 53 -j REDIRECT --to-ports 53

# routes.txt: <tunnel_ip> <local_port> <upstream_url> per line.
while read -r ip port url; do
  [ -n "$ip" ] || continue
  iptables -t nat -A PREROUTING -i tun0 -s "$ip" -p tcp \
    -j REDIRECT --to-ports "$port"
  gost -L "red://:$port" -F "$url" &
done < /etc/ispvpn/routes.txt

iptables -A INPUT -i tun0 -j ACCEPT
# Non-TCP/non-DNS (QUIC, ICMP, other UDP): drop -> client falls back to TCP.
# (REJECT target needs xt_REJECT, absent on this host kernel — DROP works.)
iptables -A FORWARD -i tun0 -j DROP

wait
