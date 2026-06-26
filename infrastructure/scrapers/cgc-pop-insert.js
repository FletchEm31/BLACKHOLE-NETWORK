#!/usr/bin/env node
// Insert CGC population-report JSON into eventhorizon.pop_reports.
// Idempotent: upserts on (grader, card_set, card_name, card_number, grade).
//
// Schema must already exist — apply once:
//   sudo -u postgres psql -d eventhorizon -f sql/pop-reports-schema.sql
//
// Usage:
//   npm i pg
//   PGPASSWORD='...' node cgc-pop-insert.js ./team-rocket-1st-edition.json [more.json ...]
//
// Env:
//   PGHOST     default <BHN_WG_LA_IP>
//   PGPORT     default 5432
//   PGDATABASE default eventhorizon
//   PGUSER     default ehuser
//   PGPASSWORD required (no default; never commit)
//   PGSSLMODE  optional ("require" enables TLS)

const fs = require('fs');
const path = require('path');
const { Client } = require('pg');

const inputs = process.argv.slice(2);
if (inputs.length === 0) {
  console.error('usage: cgc-pop-insert.js <json> [<json> ...]');
  process.exit(2);
}
if (!process.env.PGPASSWORD) {
  console.error('error: PGPASSWORD env var is required');
  process.exit(2);
}

const PG = {
  host: process.env.PGHOST || '<BHN_WG_LA_IP>',
  port: parseInt(process.env.PGPORT || '5432', 10),
  database: process.env.PGDATABASE || 'eventhorizon',
  user: process.env.PGUSER || 'ehuser',
  password: process.env.PGPASSWORD,
  ssl: process.env.PGSSLMODE === 'require' ? { rejectUnauthorized: false } : false,
};

const TABLE_CHECK_SQL = `SELECT to_regclass('public.pop_reports') AS regclass`;

const UPSERT_SQL = `
INSERT INTO pop_reports
  (grader, card_set, card_name, card_number, grade, population, source_url, scraped_at)
SELECT * FROM UNNEST(
  $1::text[], $2::text[], $3::text[], $4::text[],
  $5::text[], $6::int[],  $7::text[], $8::timestamptz[]
)
ON CONFLICT (grader, card_set, card_name, card_number, grade) DO UPDATE
SET population = EXCLUDED.population,
    source_url = EXCLUDED.source_url,
    scraped_at = EXCLUDED.scraped_at
RETURNING (xmax = 0) AS inserted
`;

function loadRecords(file) {
  const abs = path.resolve(file);
  const raw = fs.readFileSync(abs, 'utf8');
  const data = JSON.parse(raw);
  if (!Array.isArray(data)) {
    throw new Error(`${file}: expected JSON array, got ${typeof data}`);
  }
  const records = [];
  for (const [i, r] of data.entries()) {
    if (
      typeof r !== 'object' ||
      r === null ||
      !r.card_name ||
      !r.grade ||
      r.population === undefined ||
      r.population === null
    ) {
      console.error(`${file}[${i}]: skipping malformed record:`, r);
      continue;
    }
    records.push({
      grader: r.grader || 'CGC',
      card_set: r.set || r.card_set || '',
      card_name: String(r.card_name),
      card_number: r.card_number ? String(r.card_number) : '',
      grade: String(r.grade),
      population: parseInt(r.population, 10),
      source_url: r.source_url || null,
      scraped_at: r.scraped_at || new Date().toISOString(),
    });
  }
  return records;
}

function chunk(arr, size) {
  const out = [];
  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));
  return out;
}

async function upsertBatch(client, batch) {
  const cols = {
    grader: [], card_set: [], card_name: [], card_number: [],
    grade: [], population: [], source_url: [], scraped_at: [],
  };
  for (const r of batch) {
    cols.grader.push(r.grader);
    cols.card_set.push(r.card_set);
    cols.card_name.push(r.card_name);
    cols.card_number.push(r.card_number);
    cols.grade.push(r.grade);
    cols.population.push(r.population);
    cols.source_url.push(r.source_url);
    cols.scraped_at.push(r.scraped_at);
  }
  const res = await client.query(UPSERT_SQL, [
    cols.grader, cols.card_set, cols.card_name, cols.card_number,
    cols.grade, cols.population, cols.source_url, cols.scraped_at,
  ]);
  const inserted = res.rows.filter((row) => row.inserted).length;
  const updated = res.rows.length - inserted;
  return { inserted, updated };
}

(async () => {
  const client = new Client(PG);
  await client.connect();
  console.error(`connected: ${PG.user}@${PG.host}:${PG.port}/${PG.database}`);

  const check = await client.query(TABLE_CHECK_SQL);
  if (!check.rows[0].regclass) {
    await client.end();
    console.error(
      'error: table public.pop_reports does not exist. Apply schema first:\n' +
      '  sudo -u postgres psql -d eventhorizon -f sql/pop-reports-schema.sql'
    );
    process.exit(3);
  }

  let grandIns = 0, grandUpd = 0, grandSkip = 0;
  for (const file of inputs) {
    const records = loadRecords(file);
    if (records.length === 0) {
      console.error(`${file}: no valid records, skipping`);
      grandSkip += 1;
      continue;
    }
    let ins = 0, upd = 0;
    for (const batch of chunk(records, 500)) {
      const { inserted, updated } = await upsertBatch(client, batch);
      ins += inserted;
      upd += updated;
    }
    console.error(`${file}: total=${records.length} inserted=${ins} updated=${upd}`);
    grandIns += ins;
    grandUpd += upd;
  }

  await client.end();
  console.error(
    `done. files=${inputs.length} skipped=${grandSkip} inserted=${grandIns} updated=${grandUpd}`
  );
})().catch((e) => {
  console.error('fatal:', e.message);
  process.exit(1);
});
