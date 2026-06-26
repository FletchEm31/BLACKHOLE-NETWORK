# FRA SOCKS5 tunnel — scrape egress runbook

> **RETIRED 2026-05-28.** Frankfurt was destroyed; this egress path no longer exists. The current eBay scrape egress uses `curl_cffi` impersonation (`impersonate="firefox144"`) directly from LA's own IP, which returns real listings — the TLS fingerprint, not the IP, was the dominant block. See the project memory `project_ebay_tls_fingerprint_impers_2026-05-28`. This runbook is preserved as history of the FRA-era approach.

---

When the operator wants to resume eBay scraping (held since the 2026-05-27 403
against LA's IP `<BHN_LA_PUBLIC_IP>`), route through Frankfurt's untouched IP.

## Why

- Hillsboro's `<BHN_HIL_PUBLIC_IP>` is the LA egress for `curl`/n8n/grafana but **NOT** for Node native `fetch()`. Node fetch bypasses `HTTPS_PROXY` env vars, so the scraper hits eBay direct from LA's own public IP `<BHN_LA_PUBLIC_IP>`.
- That IP got 403'd by eBay on 2026-05-27 at 01:35:59 UTC.
- Frankfurt's `192.248.187.208` has never made an eBay request for this operator. Clean slate.

## Start the tunnel

Run on **LA**, inside a `screen` or `tmux` session so it survives the SSH disconnect:

```bash
# on LA
screen -S fra-socks
ssh -i /root/.ssh/eh_frankfurt \
    -o ConnectTimeout=10 -o ServerAliveInterval=30 -o ServerAliveCountMax=4 \
    -o ExitOnForwardFailure=yes \
    -N -D 127.0.0.1:10808 root@10.9.0.2
# detach screen: Ctrl-A, then D
```

Now LA has a local SOCKS5 endpoint at `socks5h://127.0.0.1:10808`. Any process on LA that points at it will egress through FRA.

## Verify it's up

```bash
# from LA (anywhere)
ss -tln | grep 10808     # should show 127.0.0.1:10808 LISTEN
curl -s --socks5-hostname 127.0.0.1:10808 https://api.ipify.org
#   → expect: 192.248.187.208  (FRA's IP)
```

## Point the scraper at the tunnel

The scraper code change is still pending — node's native `fetch()` does not
honor `HTTPS_PROXY` env vars (proven 2026-05-27). You'll need either:

- **Quick path:** edit `infrastructure/scrapers/ebay-sold-scrape.js` `fetchPage()`
  to use undici's `Agent` with a custom `connect` callback that opens the
  socket through `socks-proxy-agent` (or `socks` package directly), then pass
  `dispatcher: agent` to `fetch()`. Add to scraper config:
  ```json
  "proxy": { "enabled": true, "dispatcher_url": "socks5h://127.0.0.1:10808" }
  ```
- **Hacky path:** drop a few lines at the top of `ebay-sold-scrape.js`:
  ```js
  const { setGlobalDispatcher, Agent } = require('undici');
  const { SocksClient } = require('socks');
  setGlobalDispatcher(new Agent({
    connect: async (opts) => {
      const { socket } = await SocksClient.createConnection({
        proxy: { host: '127.0.0.1', port: 10808, type: 5 },
        command: 'connect',
        destination: { host: opts.hostname, port: opts.port },
      });
      if (opts.protocol === 'https:') {
        return require('tls').connect({ ...opts, socket });
      }
      return socket;
    },
  }));
  ```
  Add `"socks": "^2.x"` to `infrastructure/scrapers/package.json` deps.

Both paths route every `fetch()` call in the scraper through FRA.

## Stop the tunnel

`screen -r fra-socks`, then Ctrl-C the ssh. Or `pkill -f "ssh.*-D 127.0.0.1:10808"`.

When the FRA node is decommissioned, tear down the screen + remove the runbook.

## Caveats

- **Single point of failure.** One tunnel, one IP. If FRA goes down mid-scrape, every request fails until reconnect.
- **DE geography.** eBay.com serves US content fine to any IP, but if the operator is personally logged into eBay from a US residential IP and the scraper hits with a DE IP simultaneously, that's a behavioral signal eBay could flag. Cookie-stripping + `credentials: 'omit'` in the scraper means scraper requests are always guest sessions — should be fine, but worth noting.
- **No rotation.** Single exit IP. If FRA gets 403'd, no fallback.
