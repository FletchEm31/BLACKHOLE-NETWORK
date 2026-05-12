# BHN Tor non-exit middle relay — Hillsboro egress-proxy node.
# Build is identical to ../tor-relay/Dockerfile (Frankfurt) and
# ../tor-relay-nj/Dockerfile. Kept duplicated rather than symlinked/shared
# because Windows clones don't preserve symlinks and per-node directories
# make deploy paths unambiguous. With three relays now, consider consolidating
# in a follow-up — the only delta across the three is torrc.

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
# SocksPort — bound to tunnel IP via compose (10.8.0.6 on LA's wg0)
EXPOSE 9050/tcp

CMD ["tor", "-f", "/etc/tor/torrc"]
