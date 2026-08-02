# 法條溯源 · tw-law-blame

Taiwanese law in **git-blame style** — for any article, see *when* it changed, *who*
proposed it, and *why*, with links to the official source.

Built entirely on 立法院 open data. No scraping.

## Data sources

| Dataset | Endpoint | Gives | Coverage |
|---|---|---|---|
| 法律條文對照表 | `odw/ID19Action.action` | `activeLaw` (before), `reviseLaw` (after), `description` (立法理由) | 第8屆 → 第10屆會期1 (see below) |
| 議案提案 | `odw/ID20Action.action` | `billProposer` / `billOrg` (提案人), `billStatus` | 第8屆 onward |
| 三讀通過條文 | `openDatasetJson.action?id=373` | enacted-text file per passed bill | **第10–11屆 only (~2020)**, 642 records |

### The publication lag is the reason this runs daily

Verified 2026-08-02: ID19 has 第8屆 and 第9屆 complete, 第10屆 only 會期1, and **nothing
for 第11屆** — the current legislature. The comparison tables are published well behind
the sittings. That is not a bug in the fetcher (each of those is a clean 200 with an
empty `dataList`, re-checked with padded params).

So the daily job is not decoration: it is how 第11屆 enters the corpus on the day 立法院
publishes it. The site states its own coverage from `index.json` rather than implying
it is current.

## Honest scope

Two tiers, and the UI says so:

- **~2020 → now** — full blame: proposed text, rationale, proposer, *and* enacted wording.
- **~2012 → 2020** — attribution and rationale only. The "after" text is what a bill
  **proposed**; committees can amend before 三讀. Enacted wording must be taken from
  全國法規資料庫's consolidated text.

A civic tool that presented proposal text as enacted law would be worse than no tool.

## Integration notes (each of these cost real debugging time)

1. **Legacy TLS required.** OpenSSL 3 rejects `data.ly.gov.tw` outright
   (`unsafe legacy renegotiation disabled`). Needs `UnsafeLegacyRenegotiation` +
   `CipherString = DEFAULT@SECLEVEL=0`. Presents as a total outage.
2. **Zero-pad *both* `term` and `sessionPeriod`.** `term=8&sessionPeriod=1` → 0 rows;
   `term=08&sessionPeriod=01` → 3319 rows. HTTP 200 either way. This one cost a full
   ingest run that reported "1/32 periods ok" and looked like a coverage problem.
3. **Over-specified params silently empty.** `term=10&sessionPeriod=03&sessionTimes=04` → 15 bytes, HTTP 200.
4. **Two API families.** `odw/ID*Action` returns `{"dataList":[...]}`;
   `openDatasetJson.action` returns `{"jsonList":[...]}`. Wrong envelope key → zero rows.
5. **`odw/ID373Action` serves HTML, not JSON.** No error, wrong content type.
6. **Use `pdfUrl`, not `docUrl`.** The `.doc` is legacy CP950 CDF; most converters can't read it.

**Every one of these returns a successful-looking response with no usable data.**
The ingester asserts on record count, never on HTTP status.

## What it does

**https://hakanai-ai.github.io/tw-law-blame/**

- **Blame view** — per article, every bill that proposed changing it: segment-level
  diff, 立法理由, 提案人, 三讀 status, link to the official PDF.
- **現行條文** — what the article says *now*, from 全國法規資料庫, alongside the proposals.
- **立法沿革** — every amendment since the statute was created. 食品安全衛生管理法 goes
  back to 民國64年 (1975), four decades before 立法院's open data starts.
- **News → law → blame** — 中央社 · 公視 · 聯合報 headlines matched to the statutes they
  name, so a story leads to that law's history. Headline and link only; never article text.
- **Jargon lookup** — 1,837 terms mined from the statutes' own 定義 articles. A term like
  主管機關 is defined by 229 statutes, so when a headline names a law, that law's
  definition wins; generic hits are labelled as such.
- **Search** — law names corpus-wide, full text within a law, 三讀-only filter, deep links.

## Sources

| What | Where | Gives |
|---|---|---|
| Proposals | 立法院 ID19 ⨝ ID20 | proposed text, 立法理由, 提案人, 三讀 status |
| Enacted law | 全國法規資料庫 `api/Ch/Law/JSON` | current article text, 立法沿革, term definitions |
| News | 中央社 (RSS) · 公視 (Atom) · 聯合報 (RSS) | headlines only |

`api/Ch/Law/JSON` ignores its `?ID=` parameter and returns the **whole** corpus as a zip
containing `ChLaw.json` (1,346 statutes, 6.1MB). One download, no per-law scraping.

## Layout

```
ingest/fetch.py     立法院 proposals -> per-law shards
ingest/enacted.py   全國法規資料庫 -> current text + 沿革
ingest/terms.py     定義 articles -> jargon glossary
ingest/news.py      RSS/Atom -> headline/statute/term matches
web/                static front-end (no build step, no dependencies)
data/               generated, NOT committed (93MB, reproducible)
```

## Licence

Code MIT. Legislative data belongs to 立法院 / the public record; this project
redistributes derived indexes and links to official sources.
