# Load Test & Scalability Audit — WorldCovers (issue #59)

**Author:** Reese (with Claude Code) · **Date:** 2026-08-05 · **Status:** ready for Reese's review — pending: soak + tier re-run on woco.dev (needs Michael's OK)

## 0. What a "load test" is, and which kind this is

"Load testing" is a family of tests distinguished by *what you scale*:

| Type | What you scale | Question answered |
|---|---|---|
| Smoke | Nothing (1 user, few requests) | Does it work at all? |
| Load | Concurrent users at expected levels | Acceptable under normal traffic? |
| Stress | Users beyond expected levels | Where and how does it break? |
| Soak | Time (hours at steady load) | Do leaks accumulate? |
| **Volume / capacity** | **Data in the database** | How does behavior change as the DB grows? |

Michael's question — "how many records can we realistically shove in here" — is a
**volume test**, and that is the core of this report. A modest concurrent-user
(load) test and a mini-soak are included secondarily. Key metrics: latency
percentiles (p50/p95 — averages hide pain), error rate, process RSS (resident
memory), and on-disk table sizes.

## 1. Where the 4 GB actually goes today (prod baseline, 2026-08-04)

Read-only observation of hellowoco.app (4 GB Linode, MariaDB 10.11 — not MySQL 8
as the docs assume; 512 MB swap present, unused):

| Consumer | Resident memory |
|---|---|
| mariadbd | ~190 MB |
| gunicorn × 5 workers (~94 MB each) + master | ~495 MB |
| nginx, fail2ban, journald, system | ~150 MB |
| **Total in use** | **~1.0 GB of 3.8 GB** (rest is cache/free) |

The entire `worldcovers` database is **11.1 MB** for 4,007 markings
(~2.8 KB/marking → ~2.8 GB projected at 1M rows). The 128 MB InnoDB buffer pool
is 87% free with a 99.99% hit ratio. **Steady-state prod has no memory problem
at all** — the 2 GB→4 GB upgrade responded to a symptom (slowness) whose cause
is query shape, not RAM (see §2). The documented deploy-time spike (Vite
`npm run build` on the box, which OOMed 1 GB) remains a separate, real
memory consumer, as are bundle imports.

Other baseline findings:

- **`DEBUG=True` in production (urgent).** `/srv/woco/backend/.env` has no
  `DEBUG` line and `settings.py:33` defaults it to `True`; a probe of a bogus
  URL returns Django's technical debug page. Security exposure + per-worker
  memory tax. One-line fix + restart, needs Michael's go-ahead.
- **No cron jobs or timers of any kind** (root and wocod crontabs empty): the
  existing `prune_revisions`, `purge_recycle_bin`, `clearsessions` commands
  never run, and **no scheduled database backups exist**.
- Audit tables (`reversion_version` + `SubmissionTransactions`) already hold
  ~3 MB — ~3× the Markings table itself — after only ~115 logged transactions.
  Write amplification is real (each API edit writes ~5 copies of the record)
  but is a *disk growth* concern, not the cause of the slowness.
- 723 of 11,493 temp tables were created *on disk* — the query-shape problem
  (DISTINCT/filesort over joins) is visible even at 4k records.

## 2. Volume test: latency vs. record count (the core finding)

Method: dedicated local DB (`worldcovers_voltest`), real merged-state bundle
(5,576 markings) clone-amplified by `seed_volume_test` preserving all data-shape
ratios (dates/citations per marking, catalog_txt duplication, post-office and
region fan-out; 5% of markings get covers). Served by gunicorn with
`DEBUG=False`, timed by `tools/loadtest/measure_endpoints.py` (p50 over ≥5
iterations, single user, local — i.e. *best case*; prod adds network + nginx +
contention).

| Markings | Default list p50 | Year-filtered p50 | Text search p50 | town-options p50 (size) |
|---|---|---|---|---|
| 5,576 | 0.68 s | 0.92 s | 0.31 s | 0.03 s (74 KB) |
| 10,000 | 1.2 s | 1.7 s | 0.48 s | 0.02 s (156 KB) |
| 50,000 | 6.2 s | 8.8 s | 2.1 s | 0.09 s (751 KB) |
| 100,000 | 13.5 s | 15.8 s | 4.5 s | 0.26 s (1.4 MB) |
| 500,000 | 60.3 s | **timeout >120 s** | 30.3 s | 1.3 s (6.1 MB) |
| 1,000,000 | **145 s** | **172 s** | 46.2 s | 2.9 s (11.1 MB) |

(1M measurements used fewer iterations — each request runs for minutes — and a
raised 280 s client timeout; `include_count=false` at 1M still costs 120 s and
147 s respectively, confirming the annotated SELECT, not COUNT, dominates.)

**Every marking-list request costs ~0.12 ms × (total rows in the table), even
for a page of 10.** That is textbook O(N)-per-request behavior where pagination
should be near-constant. The cause (verified by EXPLAIN ANALYZE, §3): the
`with_date_range()` annotation attaches 8 correlated subqueries per row and the
default ordering joins across the post-office→region junction, so MySQL/MariaDB
evaluates the annotations for the whole table before applying `LIMIT 10`.

Consequences at prod's own scale:

- At **10k records — where prod will be after the next state lands — every
  search page already costs ~1.2 s** of pure server time. With 5 synchronous
  gunicorn workers, six simultaneous users queue behind each other. This is
  the "everything chugs" wall; it was never RAM.
- `include_count=false` (built but unused by the frontend) saves little here,
  because the annotated SELECT — not just COUNT — carries the cost.
- Text search is ~3× cheaper but equally linear (double full-scan + DISTINCT).
- `town-options` (unpaginated, fetched on every submission-form mount) grows
  linearly to an 11 MB / 4.7 s response at 1M rows.
- MySQL memory stayed modest throughout (mysqld RSS ~170→410 MB from 5.5k to
  1M rows): **the engine is fine; the query shape is the whole problem.**
  The FileMaker comparison resolves the same way — FileMaker isn't doing 50M
  rows with these queries; nothing about MySQL prevents 1M+ rows here once
  the date range is a real indexed column.

## 3. EXPLAIN ANALYZE evidence

Captured at the 1M tier against the exact SQL Django emits (full plans in
`tools/loadtest/explain_1M.txt`). The default list page (`LIMIT 10`):

```
-> Limit: 10 row(s)                       (actual time=145902..145902 rows=10)
  -> Sort row IDs: common_region.name, Markings.is_manuscript,
                   post_office.name, earliest_seen
    -> Table scan on <temporary>          (actual rows=1.21e+6)
      -> Temporary table                  (actual time=142086..142086 rows=1.21e+6)
        -> Nested loop left join          (rows=1.21e+6)  ...
```

To return **10 rows**, the server materializes a **1.21M-row temporary table**
(every marking, fanned out ×1.2 by the multi-valued region join — no
`.distinct()`), evaluating the eight `with_date_range()` annotations while
doing so (~142 of the 146 seconds), sorts it, and only then applies the LIMIT.

The year-filtered variant shows the per-row re-execution directly:

```
-> Select #50 (subquery in condition; dependent)
   -> Limit: 1 row(s)   (actual time=0.006 rows=0.965 loops=1.21e+6)
      -> Sort: U0.date, U0.id, limit input to 1 row(s) per chunk
```

A **dependent subquery executed 1,210,000 times** (`loops=1.21e+6`) — once per
marking row, each with its own sort — and there are eight of them (Selects
#50–#57). Because `earliest_use_year_min`/`latest_use_year_max` filter *on the
annotation*, no filter can be pushed below the subqueries; the whole table must
be annotated before a single row qualifies.

Secondary observations from the same capture: 44–46 SQL statements per list
page (the per-row image lookup in `MarkingListSerializer`), and the
recycle-bin anti-join on every query.

## 4. Concurrent-user test (Locust)

Locust scenarios replay real frontend behavior (dashboard mount = the actual
paged dropdown fetches + `town-options`; browse/search/detail with realistic
weights). Run against the 100k-tier DB, gunicorn with prod's 5 sync workers.

**Queueing collapse, quantified.** `/colors/` — an 8 ms endpoint with one
user — takes a **median 8.5 s (max 14 s) with just 10 concurrent users**,
because all 5 synchronous workers are occupied by multi-second marking-list
queries. Head-of-line blocking makes *every* page load slow once a handful of
users are searching:

| Endpoint | 1 user | 5 users | 10 users |
|---|---|---|---|
| `/colors/` (8 ms solo) | 9 ms | 24 ms–840 ms | **8.5 s median** |
| `/markings/` browse | ~13.5 s | 16 s | 28–39 s |
| `/markings/` year filter | ~16 s | 23 s | (worker-starved) |
| Aggregate throughput | — | 0.25 req/s | **0.28 req/s** (10 users!) |

Ten users generated only 18 completed requests in 2.5 minutes — the server is
fully saturated at **~0.3 requests/second**.

**OOM reproduction (the prod failure mode).** In an earlier run where client
requests hung instead of timing out (an SSL-redirect misconfiguration in the
harness — but behaviorally identical to real users stacking retries on stuck
pages), 10 concurrent users drove the kernel to OOM-kill gunicorn workers
(`Worker (pid) was sent SIGKILL! Perhaps out of memory?` in the gunicorn log,
workers respawning in a loop, site effectively down). With orderly 180 s
client timeouts the same load survives at unusable latency instead. **This is
the mechanism behind prod's "everything chugs → upgrade the RAM" incident:
the RAM pressure is a *consequence* of query latency under concurrency, not
the cause.** Caveat: the test box had ~3.5 GB available (comparable to prod's
headroom) but shares the machine with other processes; the 60-minute soak
should be re-run on woco.dev before quoting exact user counts.

## 5. Ingest measurements

- `seed_volume_test` (bulk_create, batches of 2,000, audit stack bypassed)
  inserted ~950k markings + ~1.5M dates_seen + ~950k citations + 320k post
  offices in **281 s** (~12k rows/s total) with flat memory — demonstrating
  the DB ingests fine when writes are batched.
- The production importer (`import_ascc_bundle`) processes rows one at a time
  (~8 queries per marking), retains three per-row copies in RAM for the whole
  run, and wraps a row-wise DELETE of 16 tables plus the entire bundle in one
  transaction. RSS profiling of a current-format bundle: TBD (the local
  legacy-format bundles predate the #88 munger rewrite and no longer match the
  importer's code-keyed schema — flagged as its own small finding).

## 6. Recommendations (ranked: impact × confidence ÷ effort)

### Do immediately (ops/config; no code risk; each needs Michael's go-ahead)

1. **Set `DEBUG=False` in `/srv/woco/backend/.env` + restart.** Security
   exposure live today; also stops per-worker debug bookkeeping. One line.
2. **Schedule the existing maintenance commands + backups.** systemd timers
   for `prune_revisions`, `purge_recycle_bin`, `clearsessions`, and a nightly
   `mariadb-dump` to `backups/`. All commands already exist; nothing runs them.
3. **gunicorn hygiene:** add `--max-requests 1000 --max-requests-jitter 100`
   (leak insurance) to `deploy/worldcovers.service` (and its validator).
4. **`CONN_MAX_AGE=60`** in settings — stop paying per-request MySQL connects.

### Structural fix #1 — the one that changes the scaling class

**Materialize `earliest_seen`/`latest_seen` (+granularity) as real, indexed
columns on `Marking`, maintained on write** (DateSeen/CoverMarking saves are
low-frequency editor actions; recompute-on-write is cheap). List requests then
filter/order on indexed columns and pagination returns to O(page). This retires
`with_date_range()` from the hot path — the 145 s page at 1M becomes
milliseconds. Everything else in this report is second-order compared to this.

Companions in the same change: default ordering must drop the multi-valued
`post_office__post_office_regions__region__name` join (denormalize a
`primary_region_name` onto Marking or PostOffice, or order by an indexed
column), and the list serializer's per-row image query becomes a single
batched prefetch in the view.

### Second tier (after the above lands)

- **Paginate or cache `/post-offices/town-options/`** (11 MB / 4.7 s at 1M;
  fetched on every submission-form mount).
- **Wire up the mitigations already built:** frontend sends
  `include_count=false` + a capped count; dropdown fetch loops request
  `page_size=100` instead of 50 round-trips of 10.
- **Text search:** replace the OR-of-two-scans + DISTINCT
  (`CitationAwareMarkingSearchFilter`) with a MySQL FULLTEXT index (or defer
  to a search engine if faceting needs grow). Linear scans of `catalog_txt`
  don't survive 1M rows even with the date fix.
- **Missing `Marking` indexes** (`type`, `is_reviewed`, dimension fields) —
  one migration, guided by the EXPLAIN captures.
- **Importer rework** for 100k+ bundles: `use_bulk=True`, `skip_diff=True`,
  `store_row_values=False`, chunked transactions (the seeder proves the DB
  side ingests at ~12k rows/s when batched).

### Worth doing, lower urgency

- Build the frontend in CI and rsync `dist/` — removes the Vite-on-box OOM
  (the original reason this box needed 2 GB) and shortens deploys.
- Reduce `FILE_UPLOAD_MAX_MEMORY_SIZE`/`DATA_UPLOAD_MAX_MEMORY_SIZE` from
  100 MB to the 2.5 MB default (stream uploads to disk instead of RAM).
- Consolidate the three overlapping audit systems (SubmissionTransaction +
  MarkingVersion/CoverVersion + django-reversion) — ~5 stored copies per edit
  today; disk-growth, not latency. Scope reversion registration to the models
  that need it.
- Deduplicate `catalog_txt` (2.74× duplication) if disk growth ever matters;
  at 2.8 KB/marking it is not urgent.
- MariaDB/MySQL tuning (`innodb_buffer_pool_size` ≈ 1 GB at the 1M tier) —
  deliberately *last*: with the query fix the working set is index-sized, and
  today's 128 MB pool is 87% free. Config was never the bottleneck.

## 7. Capacity recommendation (feeds hosting decision #62)

**As the code stands today, no amount of hardware reaches 1M records.** Every
search page costs ~0.12 ms × total-rows of server time; at 1M records that is
145+ s per page — a bigger box moves that number by a constant factor at best.
The 2 GB→4 GB upgrade bought OOM headroom, not capacity. Practical limits with
current code: usable feel (<1 s pages) ends around **~8k records**; by ~50k
records a *single* user waits 6+ s per page and a handful of concurrent users
saturate the site (~0.3 req/s aggregate).

**With the structural fix** (materialized, indexed date-range columns +
ordering/N+1 companions): list pages become indexed lookups — milliseconds,
independent of table size. The whole 1M-record dataset is ~2.8 GB on disk;
a 4 GB box with a properly sized buffer pool (~1 GB) serves it comfortably
for this audience (tens of concurrent society users). At that point the
**current $50/mo 4 GB Linode is sufficient through 1M+ records**; the doubled
tier is unnecessary on data-volume grounds alone, and a Society-infra
migration (~$2,500) is not justified by capacity.

Recommended sequence: quick wins now (§6 items 1–4, essentially free) →
structural fix as the next engineering milestone → re-run this suite (one
command per tier, §Appendix) on woco.dev to certify the numbers on real
hardware → then decide #62 with data.

Suggested acceptance target for the re-test: **p95 < 1 s for search at 1M
records with 25 concurrent users on the 4 GB box** — comfortably achievable
with indexed date columns; unreachable by any hardware without them.

## Appendix: reproduction

- Seeder: `backend/common/management/commands/seed_volume_test.py` (voltest-guarded)
- Legacy bundle loader: `backend/common/management/commands/load_legacy_bundle.py`
- Timing harness: `tools/loadtest/measure_endpoints.py` (accumulates `results.csv`)
- Locust scenarios: `tools/loadtest/locustfile.py`
- Raw results: `tools/loadtest/results.csv` (committed with final report)
