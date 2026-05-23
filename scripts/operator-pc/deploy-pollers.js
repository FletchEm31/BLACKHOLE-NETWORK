#!/usr/bin/env node
/*
 * deploy-pollers.js
 *
 * Deploys the 11 BHN API pollers to their canonical nodes per each script's
 * docstring, with a /etc/cron.d/<name> entry on each side.
 *
 * Runs from operator PC; requires SSH access to:
 *   - root@10.8.0.1            (LA)
 *   - root@140.82.4.35 :2222   (NJ)
 *
 * Each poller's behavior on missing API key / env file is "graceful" - log + exit 0 -
 * so half-configured state won't break cron or other workflows. Env files (with API
 * keys) are the operator's responsibility post-deploy.
 *
 * One-shot deploy; re-runnable as needed.
 *
 * Usage:
 *   node scripts/operator-pc/deploy-pollers.js
 */

'use strict';

const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const SCRIPTS_DIR = path.join(REPO_ROOT, 'scripts');
const CRON_DIR_REPO = path.join(REPO_ROOT, 'infrastructure', 'cron.d');

// SSH connection details per node
const NODE = {
    la: { ssh: ['ssh', '-o', 'BatchMode=yes', 'root@10.8.0.1'],
          scp: ['scp', '-q', '-o', 'BatchMode=yes'],
          dest: 'root@10.8.0.1' },
    nj: { ssh: ['ssh', '-o', 'BatchMode=yes', '-p', '2222', 'root@140.82.4.35'],
          scp: ['scp', '-q', '-o', 'BatchMode=yes', '-P', '2222'],
          dest: 'root@140.82.4.35' },
};

// ---------------------------------------------------------------------------
// Manifest - 11 pollers, schedules pulled verbatim from each script's docstring
// ---------------------------------------------------------------------------
const PATH_LINE = 'PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin';
const SHELL_LINE = 'SHELL=/bin/bash';

const POLLERS = [
    // --- LA group ---
    {
        name: 'bhn-fred-poller',
        node: 'la',
        cron: `${SHELL_LINE}
${PATH_LINE}
CRON_TZ=America/New_York
# FRED releases at 08:00 and 14:00 ET (weekdays); safety-poll +1 min
0,1  8 * * 1-5  root  /usr/local/sbin/bhn-fred-poller.py >> /var/log/bhn-fred-poller.log 2>&1
0,1 14 * * 1-5  root  /usr/local/sbin/bhn-fred-poller.py >> /var/log/bhn-fred-poller.log 2>&1
`,
    },
    {
        name: 'bhn-eia-poller',
        node: 'la',
        cron: `${SHELL_LINE}
${PATH_LINE}
CRON_TZ=America/New_York
# EIA: 10:30 ET daily petroleum releases, 10:35 ET Wed weekly inventory
30,31 10 * * 1-5  root  /usr/local/sbin/bhn-eia-poller.py >> /var/log/bhn-eia-poller.log 2>&1
35,36 10 * * 3    root  /usr/local/sbin/bhn-eia-poller.py >> /var/log/bhn-eia-poller.log 2>&1
`,
    },
    {
        name: 'bhn-usda-poller',
        node: 'la',
        cron: `${SHELL_LINE}
${PATH_LINE}
CRON_TZ=America/New_York
# USDA: 08:30 ET daily releases, 15:00 ET Fri crop progress; safety-poll +1 min
30,31 8 * * 1-5  root  /usr/local/sbin/bhn-usda-poller.py >> /var/log/bhn-usda-poller.log 2>&1
0,1  15 * * 5    root  /usr/local/sbin/bhn-usda-poller.py >> /var/log/bhn-usda-poller.log 2>&1
`,
    },
    {
        name: 'bhn-coingecko-poller',
        node: 'la',
        cron: `${SHELL_LINE}
${PATH_LINE}
# CoinGecko top-N every 15 min (free tier rate-limit comfortable at this cadence)
*/15 * * * *  root  /usr/local/sbin/bhn-coingecko-poller.py >> /var/log/bhn-coingecko-poller.log 2>&1
`,
    },
    {
        name: 'bhn-tor-metrics-poller',
        node: 'la',
        cron: `${SHELL_LINE}
${PATH_LINE}
# Tor onionoo consensus pull, daily 07:15 UTC
15 7 * * *  root  /usr/local/sbin/bhn-tor-metrics-poller.py >> /var/log/bhn-tor-metrics-poller.log 2>&1
`,
    },

    // --- NJ group ---
    {
        name: 'bhn-alpaca-extras-poller',
        node: 'nj',
        cron: `${SHELL_LINE}
${PATH_LINE}
# Alpaca non-streamable feeds: corporate actions (4h), news (15m), options (mkt hrs)
0 */4 * * *        root  /usr/local/sbin/bhn-alpaca-extras-poller.py corporate >> /var/log/bhn-alpaca-extras-poller.log 2>&1
*/15 * * * *       root  /usr/local/sbin/bhn-alpaca-extras-poller.py news      >> /var/log/bhn-alpaca-extras-poller.log 2>&1
*/15 9-16 * * 1-5  root  /usr/local/sbin/bhn-alpaca-extras-poller.py options   >> /var/log/bhn-alpaca-extras-poller.log 2>&1
`,
    },
    {
        name: 'bhn-finnhub-poller',
        node: 'nj',
        cron: `${SHELL_LINE}
${PATH_LINE}
# Finnhub analyst recs + earnings, daily 06:30
30 6 * * *  root  /usr/local/sbin/bhn-finnhub-poller.py >> /var/log/bhn-finnhub-poller.log 2>&1
`,
    },
    {
        name: 'bhn-fmp-poller',
        node: 'nj',
        cron: `${SHELL_LINE}
${PATH_LINE}
# FMP watchlist quotes, every 15 min during market hours
*/15 9-16 * * 1-5  root  /usr/local/sbin/bhn-fmp-poller.py >> /var/log/bhn-fmp-poller.log 2>&1
`,
    },
    {
        name: 'bhn-kalshi-poller',
        node: 'nj',
        cron: `${SHELL_LINE}
${PATH_LINE}
# Kalshi top markets, every 10 min 24/7
*/10 * * * *  root  /usr/local/sbin/bhn-kalshi-poller.py >> /var/log/bhn-kalshi-poller.log 2>&1
`,
    },
    {
        name: 'bhn-polymarket-poller',
        node: 'nj',
        cron: `${SHELL_LINE}
${PATH_LINE}
# Polymarket top active markets, every 10 min 24/7 (no auth)
*/10 * * * *  root  /usr/local/sbin/bhn-polymarket-poller.py >> /var/log/bhn-polymarket-poller.log 2>&1
`,
    },
    {
        name: 'bhn-quiver-poller',
        node: 'nj',
        cron: `${SHELL_LINE}
${PATH_LINE}
# Quiver Quantitative congressional trading disclosures, every 15 min 24/7
*/15 * * * *  root  /usr/local/sbin/bhn-quiver-poller.py >> /var/log/bhn-quiver-poller.log 2>&1
`,
    },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function run(argv) {
    // argv-style execution; no shell parsing, so paths with spaces survive intact.
    const [cmd, ...rest] = argv;
    const r = spawnSync(cmd, rest, { stdio: 'pipe', encoding: 'utf8' });
    if (r.status !== 0) {
        const detail = (r.stderr || '').trim() || (r.stdout || '').trim() || `exit ${r.status}`;
        throw new Error(`${argv.join(' ')}\n  ${detail}`);
    }
    return r.stdout || '';
}

function log(msg) { process.stdout.write(msg + '\n'); }

function scpFile(node, localPath, remotePath) {
    run(NODE[node].scp.concat([localPath, `${NODE[node].dest}:${remotePath}`]));
}

function sshExec(node, cmd) {
    return run(NODE[node].ssh.concat([cmd]));
}

// Write the cron file content to a tmp local path then scp it up - safer
// than embedding multi-line content in an ssh heredoc through cmd.exe.
function deployPoller(p) {
    const scriptName = `${p.name}.py`;
    const localScript = path.join(SCRIPTS_DIR, scriptName);
    if (!fs.existsSync(localScript)) {
        throw new Error(`missing local script: ${localScript}`);
    }

    // Stage cron content locally first (also saved into repo for tracking)
    const cronRepoPath = path.join(CRON_DIR_REPO, p.name);
    fs.writeFileSync(cronRepoPath, p.cron);

    log(`[${p.node}] ${p.name}`);
    log(`  scp script -> /usr/local/sbin/${scriptName}`);
    scpFile(p.node, localScript, `/usr/local/sbin/${scriptName}`);

    log(`  scp cron   -> /etc/cron.d/${p.name}`);
    scpFile(p.node, cronRepoPath, `/etc/cron.d/${p.name}`);

    log(`  chmod + chown on both`);
    sshExec(p.node, `chown root:root /usr/local/sbin/${scriptName} /etc/cron.d/${p.name} && chmod 0700 /usr/local/sbin/${scriptName} && chmod 0644 /etc/cron.d/${p.name}`);

    log(`  [OK]`);
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

if (!fs.existsSync(CRON_DIR_REPO)) {
    fs.mkdirSync(CRON_DIR_REPO, { recursive: true });
    log(`Created ${CRON_DIR_REPO}`);
}

log(`Deploying ${POLLERS.length} pollers ...\n`);

const errors = [];
for (const p of POLLERS) {
    try {
        deployPoller(p);
    } catch (e) {
        errors.push({ name: p.name, node: p.node, err: e.message });
        log(`  [FAIL] ${e.message}`);
    }
    log('');
}

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------
log('=== summary ===');
const okCount = POLLERS.length - errors.length;
log(`  deployed cleanly: ${okCount}/${POLLERS.length}`);
if (errors.length) {
    log(`  failures:`);
    for (const e of errors) log(`    ${e.node}/${e.name}: ${e.err}`);
    process.exit(1);
}

log('');
log('cron files staged in repo at infrastructure/cron.d/');
log('next: smoke-test one poller per node, then cron should pick them up at their schedule.');
