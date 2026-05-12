# BHN Tor non-exit middle relay.
# Built from debian:bookworm-slim + apt tor — Tor Project doesn't publish
# an official image, and community images get stale. Small custom build
# is cleaner than pinning a third-party image with unknown maintenance.

FROM debian:bookworm-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        tor \
        tor-geoipdb \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# The `tor` package creates the `debian-tor` user/group automatically.
RUN mkdir -p /var/lib/tor /var/log/tor && \
    chown -R debian-tor:debian-tor /var/lib/tor /var/log/tor && \
    chmod 700 /var/lib/tor

COPY torrc /etc/tor/torrc
RUN chown debian-tor:debian-tor /etc/tor/torrc && \
    chmod 644 /etc/tor/torrc

USER debian-tor

# ORPort — must be publicly reachable for the relay to participate in consensus
EXPOSE 9001/tcp
# SocksPort — published via compose to tunnel IP only
EXPOSE 9050/tcp

CMD ["tor", "-f", "/etc/tor/torrc"]
