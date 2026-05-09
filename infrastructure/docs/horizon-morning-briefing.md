# HORIZON — Morning Briefing Workflow (Item 5 / M2)

Implementation blueprint for HORIZON's morning briefing — daily voice call to operator with weather, calendar, security, markets, eBay, trading opportunities, news.

Per the roadmap (M2): triggered daily at operator-defined time, ElevenLabs voice + Twilio outbound call, ~90-second briefing.

## Workflow shape

```
Schedule Trigger (07:00 PT daily, configurable) ─┐
Manual Trigger (test) ───────────────────────────┤
                                                  ▼
                                          Set: period_start = NOW()-24h, period_end = NOW(), tz = operator
                                                  │
                            ┌─────────────────────┼─────────────────────┐
                            ▼                     ▼                     ▼
                      [Weather]              [Calendar]           [Markets]
                      OpenWeatherMap         Google Cal           FMP + Alpaca
                            │                     │                     │
                            └─────────────────────┼─────────────────────┘
                                                  │
                            ┌─────────────────────┼─────────────────────┐
                            ▼                     ▼                     ▼
                      [eBay]                  [Security]          [News]
                      eBay Browse API         PG (sec events,     NewsAPI
                                              node_logs, anomalies)
                            │                     │                     │
                            └─────────────────────┼─────────────────────┘
                                                  ▼
                                       [Trading opportunities]
                                       PG (trading_rules + market_signals)
                                                  │
                                                  ▼
                                    Code: Assemble structured payload
                                                  │
                                                  ▼
                                    Sonnet 4.6: compose spoken briefing
                                    (or HORIZON_query_cascade for cache-first)
                                                  │
                                                  ▼
                                    ElevenLabs TTS: HORIZON voice → audio file
                                                  │
                                                  ▼
                                    Twilio: outbound call → play audio
                                                  │
                                                  ▼
                                    PG insert: briefing_log row
                                                  │
                                                  ▼
                                    SMS confirmation to operator (optional)
```

## Section-by-section data sources

| Section | Source | Query / API call | Status |
|---------|--------|------------------|--------|
| Greeting | Static | `Good morning, Hayden.` | ✅ |
| Weather | OpenWeatherMap | `GET /data/2.5/onecall?lat=…&lon=…&exclude=minutely,alerts&units=imperial&appid=$KEY` | ⏸ needs key |
| Calendar | Google Calendar | `GET /calendar/v3/calendars/primary/events?timeMin=…&timeMax=…&singleEvents=true&orderBy=startTime` | ⏸ needs OAuth |
| Security overnight | PG | `SELECT severity, count(*) FROM security_events WHERE detected_at >= $1 GROUP BY severity` + `node_logs` similar + `anomalies WHERE resolved=FALSE` | ✅ buildable now |
| Markets | FMP + Alpaca | FMP for SPY/BTC/QQQ overnight delta; Alpaca paper account positions | ⏸ needs keys |
| eBay | eBay Browse API + comp lookup | poll active listings against `ebay_watchlist` rows where `active=TRUE`, compare to comp avg in `market_signals` | ⏸ needs key + watchlist data |
| Trading | PG | `SELECT * FROM trading_rules WHERE active = TRUE` + `market_signals` since `last_triggered_at` | ✅ buildable now (will be empty until M6 active) |
| News | NewsAPI | `GET /v2/top-headlines?country=us&pageSize=10&apiKey=$KEY` then top-3 by relevance | ⏸ needs key |
| Closing | Static | `Have a great day, Hayden.` | ✅ |

**Two sections are buildable right now** (security overnight + trading opportunities) using only the data already in PG. The other six need API keys.

## Section assembly node (Code)

Builds the structured payload that Sonnet then turns into spoken prose:

```javascript
// Assemble structured briefing payload from upstream data nodes.
// Sonnet will consume this and produce TTS-ready text.

const period = $('Set: period').first().json;
const weather  = $('Weather').first()?.json   || { error: 'unavailable' };
const calendar = $('Calendar').first()?.json  || { events: [] };
const markets  = $('Markets').first()?.json   || { error: 'unavailable' };
const ebay     = $('eBay').first()?.json      || { messages: [], offers: [], deals: [] };
const security = $('Security').all().map(r => r.json);
const trading  = $('Trading').all().map(r => r.json);
const news     = $('News').first()?.json      || { articles: [] };

const payload = {
  period_start: period.period_start,
  period_end:   period.period_end,
  greeting:     'Good morning, Hayden.',
  weather: {
    current_temp_f: weather?.current?.temp,
    today_high_f:   weather?.daily?.[0]?.temp?.max,
    today_low_f:    weather?.daily?.[0]?.temp?.min,
    summary:        weather?.daily?.[0]?.summary,
    precipitation_chance: weather?.daily?.[0]?.pop,
  },
  calendar: {
    today_events: (calendar.events || []).map(e => ({
      title:      e.summary,
      start:      e.start?.dateTime || e.start?.date,
      attendees:  (e.attendees || []).length,
    })).slice(0, 5),
  },
  security_overnight: {
    by_severity: security,             // [{severity, count}]
    open_anomalies: ($('Anomalies').first()?.json?.count) || 0,
    blocked_ips_top_3: ($('TopBlocked').all() || []).slice(0, 3).map(r => r.json),
  },
  markets: {
    spy_pct_change_overnight: markets?.spy?.pct,
    btc_pct_change_overnight: markets?.btc?.pct,
    watchlist_movers: markets?.watchlist || [],   // top 3 movers
  },
  ebay: {
    new_messages:   ebay.messages?.length || 0,
    new_offers:     ebay.offers?.length   || 0,
    deals_found:    ebay.deals?.length    || 0,
    deals_summary:  (ebay.deals || []).slice(0, 3),
  },
  trading_opportunities: trading.map(t => ({
    symbol: t.symbol, rule: t.trigger_type, threshold: t.threshold,
    action: t.action, last_signal: t.last_triggered_at,
  })),
  news_top_3: (news.articles || []).slice(0, 3).map(a => ({
    title: a.title, source: a.source?.name,
  })),
  closing: 'Have a great day, Hayden.',
};

return [{ json: payload }];
```

## Sonnet composition prompt

```
You are HORIZON, composing a 90-second morning briefing for Hayden. The
briefing is delivered via ElevenLabs TTS and spoken aloud, so write for the
ear: short sentences, natural rhythm, no markdown, no enumeration ("First…
Second…"). Use natural transitions. Skip empty sections (don't say "no
calendar events" if there are none — just move on).

Hard rules:
- Open exactly with: "Good morning, Hayden."
- Close exactly with: "Have a great day, Hayden."
- Mention the weather summary + temp range
- Mention any calendar events (titles + times in plain language)
- Security: only mention if there's something noteworthy (open anomalies,
  unusual event counts vs the baseline of 2,000-4,000/day, or a NEW
  event_type in top_event_types). If everything is steady-state, say
  "EH security overnight was quiet."
- Markets: SPY and BTC overnight delta, then any watchlist movers >2%
- eBay: only mention if non-zero (new messages, offers, or deals found).
  For deals, name the card + the price discount vs comp.
- Trading: any rules that triggered overnight; reference rule by symbol.
- News: top 3 stories, summarized in one sentence each
- Total length: ~90 seconds spoken (≈ 200-220 words).

Respond with PLAIN TEXT only, no JSON, no markdown.

Briefing data:
{{ structured_payload }}
```

## briefing_log table (add to schema in next commit)

```sql
CREATE TABLE briefing_log (
    id              BIGSERIAL PRIMARY KEY,
    triggered_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    briefing_type   TEXT NOT NULL CHECK (briefing_type IN ('morning','evening','manual','test')),
    period_start    TIMESTAMPTZ,
    period_end      TIMESTAMPTZ,
    raw_payload     JSONB,            -- the structured assembly Sonnet sees
    spoken_text     TEXT,             -- the final TTS-ready text
    word_count      INT,
    duration_estimate_sec INT,        -- estimated based on word count
    delivery_method TEXT,             -- 'voice' | 'sms' | 'log_only'
    delivery_status TEXT,             -- 'pending' | 'sent' | 'acknowledged' | 'failed'
    delivery_metadata JSONB,          -- twilio call_sid, elevenlabs request id, etc.
    sonnet_input_tokens  INT,
    sonnet_output_tokens INT,
    sonnet_cost_usd DECIMAL(10,6),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX briefing_log_triggered_idx ON briefing_log (triggered_at DESC);
CREATE INDEX briefing_log_type_idx      ON briefing_log (briefing_type);
```

Add to `sql/horizon-schema.sql` in the next commit (held back from foundation commit so the briefing-specific decision can be reviewed).

## Phased buildout

| Phase | What's added | Buildable before accounts arrive? |
|-------|--------------|-------------------------------------|
| **A** (now) | Workflow skeleton in n8n: Schedule + Manual triggers, Set node, Security PG query, Trading PG query, Assembly Code node, **Sonnet compose**, **`delivery_method='log_only'`** — output the spoken text to `briefing_log`, no actual call placed | ✅ Yes — uses only existing PG + Anthropic credential |
| **B** (after Twilio + ElevenLabs) | Add ElevenLabs TTS node + Twilio Voice node. Flip `delivery_method='voice'`. Test a real outbound call. | ⏸ Needs Twilio + ElevenLabs Creator credentials |
| **C** (after weather/calendar/news) | Add Weather + Calendar + News HTTP nodes. Skip empty sections in Sonnet prompt. | ⏸ Needs OpenWeatherMap + Google + NewsAPI |
| **D** (after FMP + eBay live data) | Add Markets + eBay HTTP nodes. Briefing reaches full content scope. | ⏸ Needs eBay watchlist data + Alpaca paper account |

**Phase A is the right Session 1 deliverable** — operator can trigger manually, see the briefing text in `briefing_log`, validate the assembly + Sonnet compose work end-to-end. Then phases B-D are mechanical wire-ups as accounts land.

## Volume / cost notes

- One Sonnet compose per scheduled run = ~2K input tokens (structured payload + system prompt) + ~250 output tokens (the briefing text). Cost ~$0.0099. Daily.
- Monthly: ~30 × $0.0099 = **$0.30/mo for Sonnet compose**.
- ElevenLabs TTS per briefing: ~250 words × ~6 chars/word = ~1,500 chars. 30 briefings/mo = 45K chars/mo. **Creator tier 100K chars/mo** quota → 45% of monthly TTS budget for morning alone. Manageable.
- Twilio outbound voice: 90s × $0.014/min ≈ $0.021/call. Daily = **$0.63/mo**.

Total morning briefing operational cost: **~$0.94/mo** (Sonnet + Twilio voice; ElevenLabs is part of the flat $22/mo Creator subscription).

## What can be built RIGHT NOW (no waiting)

The Phase A skeleton — Schedule trigger + Set node + 2 PG queries + Assembly Code + Sonnet compose + briefing_log insert — is fully buildable today. Output is a text briefing logged to PG that operator can read manually. No external APIs touched.

When operator gives the word and provisions Twilio + ElevenLabs, Phase B wires in TTS + voice call delivery. Phases C-D follow as their respective APIs land.
