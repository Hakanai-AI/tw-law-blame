# 法條溯源 · tw-law-blame

Taiwanese law in **git-blame style** — for any article, see *when* it changed, *who*
proposed it, and *why*, with links to the official source.

Built entirely on 立法院 open data. No scraping.

## Data sources

| Dataset | Endpoint | Gives | Coverage |
|---|---|---|---|
| 法律條文對照表 | `odw/ID19Action.action` | `activeLaw` (before), `reviseLaw` (after), `description` (立法理由) | 第8屆 onward (~2012), ~2.1M rows |
| 議案提案 | `odw/ID20Action.action` | `billProposer` / `billOrg` (提案人), `billStatus` | 第8屆 onward |
| 三讀通過條文 | `openDatasetJson.action?id=373` | enacted-text file per passed bill | **第10–11屆 only (~2020)**, 642 records |

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
2. **Zero-pad period params.** `sessionPeriod=1` → empty; `=01` → data. HTTP 200 either way.
3. **Over-specified params silently empty.** `term=10&sessionPeriod=03&sessionTimes=04` → 15 bytes, HTTP 200.
4. **Two API families.** `odw/ID*Action` returns `{"dataList":[...]}`;
   `openDatasetJson.action` returns `{"jsonList":[...]}`. Wrong envelope key → zero rows.
5. **`odw/ID373Action` serves HTML, not JSON.** No error, wrong content type.
6. **Use `pdfUrl`, not `docUrl`.** The `.doc` is legacy CP950 CDF; most converters can't read it.

**Every one of these returns a successful-looking response with no usable data.**
The ingester asserts on record count, never on HTTP status.

## Layout

```
ingest/    fetchers + normaliser (Python, stdlib only)
data/      generated JSON shards, committed so Pages can serve them statically
web/       static front-end (no build step, no dependencies)
```

## Licence

Code MIT. Legislative data belongs to 立法院 / the public record; this project
redistributes derived indexes and links to official sources.
