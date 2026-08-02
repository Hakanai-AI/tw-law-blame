#!/usr/bin/env python3
"""News → law matcher.

Reads headlines from first-class Taiwanese news sources, finds which statutes
in our corpus they name, and emits `data/news.json` so the front-end can offer
"this is in the news → here is that law's amendment history".

**We store headline, link, source and date only — never article text.** The
point is to point at coverage, not to reproduce it.

Adding a source is one entry in SOURCES. Both RSS 2.0 and Atom are handled
because 中央社 publishes RSS and 公視 publishes Atom.
"""

import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
UA = "Mozilla/5.0 (compatible; tw-law-blame/0.1; +https://github.com/Hakanai-AI/tw-law-blame)"

SOURCES = [
    # 中央社 — RSS 2.0, via feedburner (cna.com.tw/rss/*.xml 404s).
    {"name": "中央社", "url": "https://feeds.feedburner.com/rsscna/politics", "topic": "政治"},
    {"name": "中央社", "url": "https://feeds.feedburner.com/rsscna/social", "topic": "社會"},
    {"name": "中央社", "url": "https://feeds.feedburner.com/rsscna/finance", "topic": "財經"},
    # 公視 — Atom, so entry/title rather than item/title.
    {"name": "公視", "url": "https://news.pts.org.tw/xml/newsfeed.xml", "topic": "綜合"},
    # Google News is a *aggregator* feed, not a first-class source: it widens
    # topical coverage so the news tab has something to search. Headlines link
    # back to the originating outlet.
    {"name": "Google News", "url": "https://news.google.com/rss/headlines/section/topic/NATION?hl=zh-TW&gl=TW&ceid=TW:zh-Hant", "topic": "國內"},
    {"name": "Google News", "url": "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=zh-TW&gl=TW&ceid=TW:zh-Hant", "topic": "財經"},
]

# Statutes are usually named in full in a headline, but a few are near-always
# referred to by a short form or by their subject. Keys are matched in the
# headline; values are the law name as it appears in our index.
ALIASES = {
    "油價": "石油管理法",
    "中油": "石油管理法",
    "台電": "電業法",
    "電價": "電業法",
    "缺電": "電業法",
    "停電": "電業法",
    "再生能源": "再生能源發展條例",
    "綠電": "再生能源發展條例",
    "勞基法": "勞動基準法",
    "個資": "個人資料保護法",
    "詐騙": "詐欺犯罪危害防制條例",
    "食安": "食品安全衛生管理法",
    "苦茶油": "食品安全衛生管理法",
    "苯駢芘": "食品安全衛生管理法",
    "下架": "食品安全衛生管理法",
}


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45) as r:
        return r.read()


def strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def entries(xml: bytes) -> list[dict]:
    """Parse RSS <item> or Atom <entry> uniformly."""
    root = ET.fromstring(xml)
    out = []
    for node in root.iter():
        if strip_ns(node.tag) not in ("item", "entry"):
            continue
        rec = {}
        for child in node:
            t = strip_ns(child.tag)
            if t == "title":
                rec["title"] = (child.text or "").strip()
            elif t == "link":
                rec["url"] = (child.get("href") or child.text or "").strip()
            elif t in ("pubDate", "published", "updated"):
                rec.setdefault("date", (child.text or "").strip())
        if rec.get("title"):
            out.append(rec)
    return out


def match_laws(headline: str, laws: list[dict]) -> list[dict]:
    """Which statutes this headline names.

    Longest name first: 「再生能源發展條例」 contains no shorter law name here,
    but 「電業法」 is a substring of 「電業法施行細則」-style names elsewhere, and
    matching the short one first would mask the specific one.
    """
    hits, seen = [], set()
    for law in sorted(laws, key=lambda l: -len(l["law"])):
        name = law["law"]
        if len(name) >= 3 and name in headline and name not in seen:
            seen.add(name)
            hits.append({**law, "via": "名稱"})
    for alias, target in ALIASES.items():
        if alias in headline and target not in seen:
            law = next((l for l in laws if l["law"] == target), None)
            if law:
                seen.add(target)
                hits.append({**law, "via": alias})
    return hits


def match_terms(headline: str, glossary: dict, matched_laws: list[dict],
                by_name: dict) -> list[dict]:
    """Legal jargon in the headline, with the statute's own definition.

    A term like 主管機關 is defined by 229 different statutes, so an arbitrary
    pick is noise. When the headline also named a law, that law's definition
    wins — 主管機關 in a food-safety story means the one in 食品安全衛生管理法,
    not the one in 政府採購法.
    """
    names = {l["law"] for l in matched_laws}
    out = []
    for term, defs in glossary.items():
        if len(term) < 3 or term not in headline:
            continue
        preferred = [d for d in defs if d["law"] in names] or defs
        # A term's defining statute is often NOT in our proposals corpus —
        # 新住民基本法 defines 新住民 but has no 對照表 rows, because ID19 has not
        # published 屆11. Carry the slug when we DO have it so the panel can link
        # to the blame view, and say so plainly when we don't.
        enriched = []
        for d in preferred[:3]:
            law = by_name.get(d["law"])
            enriched.append({**d, "slug": law["slug"] if law else "",
                             "amendments": law["amendments"] if law else 0})
        out.append({"term": term, "defs": enriched, "scoped": bool(
            [d for d in defs if d["law"] in names])})
    # Longest first: 目的事業主管機關 is more informative than 主管機關.
    out.sort(key=lambda t: -len(t["term"]))
    # Drop a term wholly contained in a longer one already matched.
    kept = []
    for t in out:
        if not any(t["term"] in k["term"] for k in kept):
            kept.append(t)
    return kept[:6]


def main() -> int:
    idx = json.loads((DATA / "index.json").read_text(encoding="utf-8"))
    laws = idx["laws"]
    by_name = {l["law"]: l for l in laws}
    try:
        tldr = json.loads((DATA / "tldr.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        tldr = {}
    try:
        glossary = json.loads((DATA / "terms.json").read_text(encoding="utf-8"))["terms"]
    except FileNotFoundError:
        glossary = {}
        print("WARN: terms.json absent — run ingest/terms.py for jargon lookup")

    items, failed = [], []
    for src in SOURCES:
        try:
            for e in entries(get(src["url"]))[:20]:
                items.append({**e, "source": src["name"], "topic": src["topic"]})
        except Exception as exc:  # noqa: BLE001 — one dead feed must not kill the run
            failed.append(f"{src['name']}/{src['topic']}: {type(exc).__name__}: {exc}")

    if not items:
        print("FATAL: every news source failed", file=sys.stderr)
        for f in failed:
            print(f"  {f}", file=sys.stderr)
        return 1

    # De-duplicate by headline — 中央社 cross-posts across topic feeds.
    seen, uniq = set(), []
    for it in items:
        if it["title"] in seen:
            continue
        seen.add(it["title"])
        it["laws"] = match_laws(it["title"], laws)
        for l in it["laws"]:
            t = tldr.get(l["slug"], {})
            l["modified"] = t.get("modified", "")
            l["enactedAmendments"] = t.get("amendments", 0)
            l["purpose"] = t.get("purpose", "")
        it["terms"] = match_terms(it["title"], glossary, it["laws"], by_name)
        uniq.append(it)

    matched = [i for i in uniq if i["laws"] or i["terms"]]
    out = {
        "generated": idx["generated"],
        "sources": sorted({s["name"] for s in SOURCES}),
        "failed": failed,
        "counts": {"headlines": len(uniq), "matched": len(matched),
                   "glossary": len(glossary)},
        # Matched first — that is the whole point of the panel.
        "items": matched + [i for i in uniq if not (i["laws"] or i["terms"])][:30],
    }
    (DATA / "news.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"{len(uniq)} headlines, {len(matched)} matched a statute")
    for f in failed:
        print(f"  FEED FAILED {f}")
    for m in matched:
        names = ", ".join(f"{l['law']}({l['amendments']}筆, via {l['via']})" for l in m["laws"]) or "—"
        print(f"  [{m['source']}] {m['title'][:44]}  ->  {names}")
        if m["terms"]:
            print("        術語: " + ", ".join(
                f"{t['term']}{'*' if t['scoped'] else ''}" for t in m["terms"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
