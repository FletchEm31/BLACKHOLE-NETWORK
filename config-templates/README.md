# config-templates/

Example configuration files for the BHN trading framework.

## `rules.example.json`

Canonical conservative-defaults `rules.json` for the 5-strategy trading
framework. Mirrors the `EXAMPLE_RULES` constant in
`scripts/trading/rules_schema.py` exactly — both must stay in sync.

### Deploy

```bash
# 1. Copy to LA's rules-source location
sudo install -o root -g root -m 0640 \
    config-templates/rules.example.json /etc/bhn/rules.json

# 2. Validate before propagating
python3 scripts/trading/validate_rules.py /etc/bhn/rules.json

# 3. Rsync to NJ + reload strategy runners
rsync -avz /etc/bhn/rules.json nj:/etc/bhn/rules.json
ssh nj 'systemctl reload bhn-strategy-runner@\*'
```

### Regenerate after schema changes

If a field is added/renamed in `rules_schema.py`, regenerate this file
to keep it in sync:

```bash
python3 scripts/trading/rules_schema.py example > config-templates/rules.example.json
```

Then `git diff` to confirm only the intended fields changed.

### What's enabled vs. paper-only

In this template every strategy has `enabled: true` and
`live_mode_approved: false`. Strategy 5's `live_execution_enabled` is
also `false` (weather side runs in signal-only mode until exchange auth
keys arrive). Flip `live_mode_approved` per-strategy after operator
sign-off and the paper-only guard in `trading_core` will gate the
transition.
