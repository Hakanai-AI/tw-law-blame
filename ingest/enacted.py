#!/usr/bin/env python3
"""Enacted law text + official amendment history, from 全國法規資料庫.

This is what closes the tool's biggest honesty gap. 立法院's ID19 gives what
was *proposed*; this gives what is actually **in force**, per article, plus the
統一的沿革 (every amendment since the statute was created — for 食品安全衛生管理法
that reaches back to 民國64年, four decades before ID19 starts).

Source: `https://law.moj.gov.tw/api/Ch/Law/JSON` — despite the `?ID=` parameter
it always returns the **whole** corpus as a zip containing `ChLaw.json`
(1,346 statutes). One download, no per-law requests.
"""

import json
import re
import ssl
import sys
import urllib.request
import zipfile
from io import BytesIO
from pathlib import Path

DUMP = "https://law.moj.gov.tw/api/Ch/Law/JSON"
DATA = Path(__file__).resolve().parent.parent / "data"
UA = "Mozilla/5.0 (compatible; tw-law-blame/0.1; +https://github.com/Hakanai-AI/tw-law-blame)"

CN = {c: i for i, c in enumerate("〇一二三四五六七八九")}


def roc_to_ad(s: str) -> str:
    """民國一百零三年二月五日 -> 2014-02-05. Returns '' if unparseable."""
    m = re.search(r"(?:中華民國)?([一二三四五六七八九十百零〇\d]+)年"
                  r"([一二三四五六七八九十百零〇\d]+)月([一二三四五六七八九十百零〇\d]+)日", s)
    if not m:
        return ""
    try:
        y, mo, d = (cn_num(x) for x in m.groups())
    except ValueError:
        return ""
    return f"{y + 1911:04d}-{mo:02d}-{d:02d}" if y and mo and d else ""


def cn_num(s: str) -> int:
    """一百零三 -> 103, 十一 -> 11, 九十 -> 90, 五 -> 5. Digits pass through."""
    if s.isdigit():
        return int(s)
    total = section = 0
    for ch in s:
        if ch in CN:
            section = CN[ch]
        elif ch in "十百千":
            total += (section or 1) * {"十": 10, "百": 100, "千": 1000}[ch]
            section = 0
    return total + section


def parse_histories(raw: str) -> list[dict]:
    """The 沿革 field is one formatted string; split it into entries.

    Shape: "1.中華民國六十四年一月二十八日總統…令制定公布全文 32 條\r\n  2.…".
    Line continuations are indented, so entries start at `N.`.
    """
    text = re.sub(r"\r?\n\s+", "", raw or "")
    out = []
    for m in re.finditer(r"(?:^|\n)\s*\d+\.(.+?)(?=(?:\n\s*\d+\.)|$)", text, re.S):
        body = " ".join(m.group(1).split())
        arts = re.findall(r"第\s*([\d、～~\-, ]+?)\s*條", body)
        out.append({
            "date": roc_to_ad(body),
            "text": body,
            "articles": arts[0].strip() if arts else "",
        })
    return out


def main() -> int:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(DUMP, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=180, context=ctx) as r:
        blob = r.read()
    print(f"downloaded {len(blob)/1e6:.1f}MB")

    with zipfile.ZipFile(BytesIO(blob)) as z:
        doc = json.loads(z.read("ChLaw.json").decode("utf-8-sig"))
    laws = doc["Laws"]
    if len(laws) < 500:
        print(f"FATAL: only {len(laws)} statutes in dump — refusing", file=sys.stderr)
        return 1
    print(f"{len(laws)} statutes, dump updated {doc.get('UpdateDate')}")

    by_name = {l["LawName"]: l for l in laws}
    idx = json.loads((DATA / "index.json").read_text(encoding="utf-8"))

    out_dir = DATA / "enacted"
    out_dir.mkdir(exist_ok=True)
    matched = 0
    for entry in idx["laws"]:
        law = by_name.get(entry["law"])
        if not law:
            continue
        matched += 1
        # `key` is the article number normalised to digits ("2", "18-1"), so the
        # site can join it to ID19 rows whose article reads 「第十八條之一」.
        arts = []
        for a in law.get("LawArticles", []):
            if a.get("ArticleType") != "A":
                continue
            no = (a.get("ArticleNo") or "").strip()
            m = re.search(r"第\s*(\d+)(?:\s*-\s*(\d+))?\s*條", no)
            arts.append({
                "no": no,
                "key": (f"{m.group(1)}-{m.group(2)}" if m and m.group(2) else m.group(1)) if m else "",
                "text": " ".join((a.get("ArticleContent") or "").split()),
            })
        (out_dir / f"{entry['slug']}.json").write_text(json.dumps({
            "name": law["LawName"],
            "url": law.get("LawURL", ""),
            "modified": law.get("LawModifiedDate", ""),
            "effective": law.get("LawEffectiveDate", ""),
            "histories": parse_histories(law.get("LawHistories", "")),
            "articles": arts,
        }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # 第1條 of a Taiwanese statute is literally its own summary — 「為管理食品衛生
    # 安全及品質，維護國民健康，特制定本法」 — so the TLDR needs no generation.
    # Kept in one small file so the news panel can show it without pulling a
    # whole enacted shard.
    tldr = {}
    for entry in idx["laws"]:
        law = by_name.get(entry["law"])
        if not law:
            continue
        first = next((a for a in law.get("LawArticles", [])
                      if a.get("ArticleType") == "A"
                      and re.search(r"第\s*1\s*條", a.get("ArticleNo") or "")), None)
        text = " ".join((first or {}).get("ArticleContent", "").split())
        tldr[entry["slug"]] = {
            "law": entry["law"],
            "purpose": text[:180],
            "modified": law.get("LawModifiedDate", ""),
            "articles": sum(1 for a in law.get("LawArticles", []) if a.get("ArticleType") == "A"),
            "amendments": len(parse_histories(law.get("LawHistories", ""))),
            "url": law.get("LawURL", ""),
        }
    (DATA / "tldr.json").write_text(
        json.dumps(tldr, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"tldr for {len(tldr)} laws")

    (DATA / "enacted" / "_meta.json").write_text(json.dumps({
        "source": "全國法規資料庫 (law.moj.gov.tw)",
        "updated": doc.get("UpdateDate"),
        "statutes_in_dump": len(laws),
        "matched_to_corpus": matched,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"matched {matched}/{len(idx['laws'])} corpus laws to enacted text")
    if matched < 100:
        print("FATAL: match rate implausibly low", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
