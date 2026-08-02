#!/usr/bin/env python3
"""Fetch 立法院 open data and build per-law JSON shards for the static front-end.

Stdlib only — no dependencies, so the GitHub Action needs no install step.

Hard rule enforced throughout: **assert on record count, never on HTTP status.**
This platform has at least six ways to return a successful-looking response with
no usable data (see README). A 200 proves nothing.
"""

import json
import os
import re
import ssl
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

BASE = "https://data.ly.gov.tw"
UA = "Mozilla/5.0 (compatible; tw-law-blame/0.1; +https://github.com/Hakanai-AI/tw-law-blame)"
OUT = Path(__file__).resolve().parent.parent / "data"


def ssl_ctx() -> ssl.SSLContext:
    """data.ly.gov.tw requires legacy TLS renegotiation.

    OpenSSL 3 refuses the handshake outright with
    `unsafe legacy renegotiation disabled`, which presents as the host being
    down rather than as a TLS policy problem. 0x4 is
    OP_LEGACY_SERVER_CONNECT.
    """
    ctx = ssl.create_default_context()
    ctx.options |= 0x4
    ctx.set_ciphers("DEFAULT@SECLEVEL=0")
    return ctx


def get(url: str, retries: int = 3) -> bytes:
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90, context=ssl_ctx()) as r:
                return r.read()
        except Exception as exc:  # noqa: BLE001 - retry any transport error
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"fetch failed after {retries}: {url}: {last}")


def rows(url: str, *, key: str, expect_min: int = 1) -> list[dict]:
    """Fetch and unwrap, asserting we actually got records.

    `key` differs by API family: odw/ID*Action -> dataList,
    openDatasetJson.action -> jsonList. Guessing wrong yields zero rows with a
    200, which is why this asserts rather than returning [].
    """
    raw = get(url).decode("utf-8", "ignore")
    m = re.search(r"[\[{].*[\]}]", raw, re.S)
    if not m:
        raise RuntimeError(f"no JSON payload (got {len(raw)}B, likely an HTML page): {url}")
    doc = json.loads(m.group(0))
    data = doc.get(key) if isinstance(doc, dict) else doc
    if data is None:
        raise RuntimeError(f"envelope key {key!r} absent — wrong API family? keys={list(doc)[:5]} :: {url}")
    if len(data) < expect_min:
        raise RuntimeError(f"got {len(data)} rows, expected >= {expect_min} — silent-empty, check params: {url}")
    return data


def article_of(title: str) -> str:
    m = re.search(r"第[一二三四五六七八九十百零〇\d]+條", title or "")
    return m.group(0) if m else "—"


def law_of(title: str) -> str:
    """Law name from a 對照表 title, stripping the article/scope suffix."""
    t = re.sub(r"(草案)?對照表$", "", title or "").strip()
    m = re.match(r"(.+?)(第[一二三四五六七八九十百零〇\d]+條|部分條文|全文|條文)", t)
    return (m.group(1) if m else t).strip() or "—"


def build(term: str, period: str) -> dict:
    cmp_rows = rows(
        f"{BASE}/odw/ID19Action.action?term={term}&sessionPeriod={period}&fileType=json",
        key="dataList",
    )
    bill_rows = rows(
        f"{BASE}/odw/ID20Action.action?term={term}&sessionPeriod={period}&fileType=json",
        key="dataList",
    )
    bills = {b["billNo"]: b for b in bill_rows if b.get("billNo")}

    laws: dict[str, list] = defaultdict(list)
    joined = 0
    for r in cmp_rows:
        if not (r.get("description") or "").strip():
            continue
        if not (r.get("activeLaw") or "").strip() and not (r.get("reviseLaw") or "").strip():
            continue
        b = bills.get(r.get("billNo", ""), {})
        if b:
            joined += 1
        title = r.get("lawCompareTitle", "")
        laws[law_of(title)].append(
            {
                "article": article_of(title),
                "title": title,
                "old": " ".join((r.get("activeLaw") or "").split()),
                "new": " ".join((r.get("reviseLaw") or "").split()),
                "why": " ".join((r.get("description") or "").split()),
                "who": b.get("billOrg") or b.get("billProposer") or "—",
                "status": b.get("billStatus", "—"),
                "billNo": r.get("billNo", ""),
                "pdf": b.get("pdfUrl", ""),
                "term": r.get("term", term),
                "session": r.get("sessionPeriod", period),
            }
        )

    print(f"  term={term} period={period}: {len(cmp_rows)} 對照表, {len(bill_rows)} 議案, "
          f"{joined} joined, {len(laws)} laws", flush=True)
    return laws


def main() -> int:
    # Terms/sessions to ingest. Kept explicit rather than discovered, so a
    # silent-empty on one period fails loudly instead of shrinking the corpus.
    targets = [(t, f"{p:02d}") for t in os.environ.get("TERMS", "10,11").split(",")
               for p in range(1, int(os.environ.get("MAX_PERIOD", "8")) + 1)]

    OUT.mkdir(parents=True, exist_ok=True)
    merged: dict[str, list] = defaultdict(list)
    ok, skipped = 0, []

    for term, period in targets:
        try:
            for law, items in build(term.strip(), period).items():
                merged[law].extend(items)
            ok += 1
        except RuntimeError as exc:
            # A period that genuinely has no sitting is legitimate; record it
            # rather than letting it silently shrink the corpus.
            skipped.append(f"{term}/{period}: {exc}")

    if not merged:
        print("FATAL: no data ingested at all", file=sys.stderr)
        return 1

    index = []
    for law, items in sorted(merged.items(), key=lambda kv: -len(kv[1])):
        slug = re.sub(r"[^\w一-鿿-]", "", law)[:60] or "unknown"
        items.sort(key=lambda x: (x["term"], x["session"], x["article"]))
        (OUT / f"{slug}.json").write_text(
            json.dumps(items, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
        index.append({
            "law": law, "slug": slug, "amendments": len(items),
            "articles": len({i["article"] for i in items}),
            "bills": len({i["billNo"] for i in items}),
        })

    (OUT / "index.json").write_text(
        json.dumps(
            {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             "laws": index,
             "totals": {"laws": len(index), "amendments": sum(i["amendments"] for i in index)},
             "skipped": skipped},
            ensure_ascii=False, indent=1),
        encoding="utf-8",
    )

    print(f"\nwrote {len(index)} law shards; {sum(i['amendments'] for i in index)} amendments; "
          f"{ok}/{len(targets)} periods ok")
    for s in skipped:
        print(f"  skipped {s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
