#!/usr/bin/env node
// Convert pop-report JSON files to a transactional SQL upsert script on stdout.
// Pipe into psql with local-socket auth (no PGPASSWORD needed):
//   node cgc-pop-load.js *.json | sudo -u postgres psql -d eventhorizon -v ON_ERROR_STOP=1
//
// Use cgc-pop-insert.js (TCP + PGPASSWORD) when the caller can't reach the postgres socket.

const fs = require('fs');

const files = process.argv.slice(2);
if (files.length === 0) {
  console.error('usage: cgc-pop-load.js <json> [<json> ...]   (writes SQL to stdout)');
  process.exit(2);
}

const esc = (s) =>
  s === null || s === undefined
    ? 'NULL'
    : "'" + String(s).replace(/'/g, "''") + "'";

const sets = [];
let totalRecords = 0;

process.stdout.write('BEGIN;\n');
process.stdout.write(
  'CREATE TEMP TABLE _stg (LIKE pop_reports INCLUDING DEFAULTS) ON COMMIT DROP;\n'
);
for (const f of files) {
  const data = JSON.parse(fs.readFileSync(f, 'utf8'));
  for (const r of data) {
    process.stdout.write(
      `INSERT INTO _stg (grader,card_set,card_name,card_number,grade,population,source_url,scraped_at) VALUES (` +
        `${esc(r.grader)},${esc(r.set)},${esc(r.card_name)},${esc(r.card_number)},` +
        `${esc(r.grade)},${parseInt(r.population, 10)},${esc(r.source_url)},${esc(r.scraped_at)});\n`
    );
    totalRecords++;
    const s = r.set;
    if (s && !sets.includes(s)) sets.push(s);
  }
}
process.stdout.write(
  'INSERT INTO pop_reports (grader,card_set,card_name,card_number,grade,population,source_url,scraped_at) ' +
    'SELECT grader,card_set,card_name,card_number,grade,population,source_url,scraped_at FROM _stg ' +
    'ON CONFLICT (grader,card_set,card_name,card_number,grade) DO UPDATE SET ' +
    'population=EXCLUDED.population, source_url=EXCLUDED.source_url, scraped_at=EXCLUDED.scraped_at;\n'
);
const setList = sets.map((s) => esc(s)).join(',');
process.stdout.write(
  `SELECT card_set, count(*) AS rows, count(DISTINCT (card_number,card_name)) AS cards, max(scraped_at) AS last_scrape ` +
    `FROM pop_reports WHERE card_set IN (${setList}) GROUP BY card_set ORDER BY card_set;\n`
);
process.stdout.write('COMMIT;\n');

console.error(
  `generated SQL for ${files.length} files, ${totalRecords} records, ${sets.length} sets`
);
