# Per-exit OpenVPN server for ISP proxy products.
# openvpn terminates the tunnel on tun0; iptables REDIRECT feeds tunneled
# TCP into gost (red handler) which chains to the product's upstream proxy;
# DNS is served by a local dnsmasq over the host's own resolver.
FROM gogost/gost:3 AS gost

FROM alpine:3.20
RUN apk add --no-cache openvpn iptables dnsmasq bash curl
COPY --from=gost /bin/gost /usr/local/bin/gost
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
