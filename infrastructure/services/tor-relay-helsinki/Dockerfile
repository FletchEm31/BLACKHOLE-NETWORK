# BHN Tor non-exit middle relay — Helsinki EU-NORTH exit node.
# Identical build to tor-relay-hillsboro/Dockerfile — only the torrc differs.
# Kept per-node to make deploy paths unambiguous on Windows clones.

FROM debian:bookworm-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        tor \
        tor-geoipdb \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p /var/lib/tor /var/log/tor && \
    chown -R debian-tor:debian-tor /var/lib/tor /var/log/tor && \
    chmod 700 /var/lib/tor

COPY torrc /etc/tor/torrc
RUN chown debian-tor:debian-tor /etc/tor/torrc && \
    chmod 644 /etc/tor/torrc

USER debian-tor

# ORPort — must be publicly reachable for the relay to participate in consensus
EXPOSE 9001/tcp
# SocksPort — bound to tunnel IP via compose
EXPOSE 9050/tcp

CMD ["tor", "-f", "/etc/tor/torrc"]
