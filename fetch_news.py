#!/usr/bin/env python3
"""各媒体のRSSを集めて、公開用のフォルダ(site/)に news.json と index.html を作る。
GitHub Actions から定期的に実行される。標準ライブラリだけで動く。"""
import html
import json
import re
import shutil
import sys
import time
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

FEEDS = {
    "ITmedia NEWS":   "https://rss.itmedia.co.jp/rss/2.0/news_bursts.xml",
    "GIGAZINE":       "https://gigazine.net/news/rss_2.0/",
    "Gizmodo JP":     "https://www.gizmodo.jp/index.xml",
    "PC Watch":       "https://pc.watch.impress.co.jp/data/rss/1.0/pcw/feed.rdf",
    "ASCII.jp":       "https://ascii.jp/rss.xml",
    "ZDNet Japan":    "https://japan.zdnet.com/rss/index.rdf",
    "マイナビ TECH+": "https://news.mynavi.jp/rss/techplus/technology",
    "ケータイ Watch": "https://k-tai.watch.impress.co.jp/data/rss/1.0/ktw/feed.rdf",
    "窓の杜":         "https://forest.watch.impress.co.jp/data/rss/1.0/wf/feed.rdf",
    "AV Watch":       "https://av.watch.impress.co.jp/data/rss/1.0/avw/feed.rdf",
}
MAX_ITEMS_PER_FEED = 35
TIMEOUT = 20
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; TechNewsDash/1.0)"}


def fetch(url, retries=2):
    last = None
    for n in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read()
        except Exception as e:  # 通信エラーは、少し待って、やり直す
            last = e
            time.sleep(2 * (n + 1))
    raise last


def to_root(data):
    """XMLを読む。UTF-8以外(Shift_JISなど)で書かれていても読めるようにする。"""
    try:
        return ET.fromstring(data)
    except (ET.ParseError, ValueError, LookupError):   # UTF-8以外(Shift_JISなど)は、読み替えてやり直す
        head = data[:200].decode("ascii", "ignore")
        m = re.search(r'encoding=["\']([^"\']+)["\']', head)
        enc = m.group(1) if m else "utf-8"
        text = data.decode(enc, "replace")
        text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text)
        return ET.fromstring(text.encode("utf-8"))


def local(tag):
    return tag.split("}")[-1]


def child_text(item, names):
    for e in item.iter():
        if e is not item and local(e.tag) in names:
            t = "".join(e.itertext()).strip()
            if t:
                return t
    return ""


def child_link(item):
    for e in item:
        if local(e.tag) == "link":
            href = e.attrib.get("href")
            if href:
                return href.strip()
            if (e.text or "").strip():
                return e.text.strip()
    return ""


def parse_date(s):
    if not s:
        return int(time.time() * 1000)
    try:
        dt = parsedate_to_datetime(s)
    except Exception:
        try:
            dt = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        except Exception:
            return int(time.time() * 1000)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def clean(s, limit=120):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def parse_feed(source, data):
    root = to_root(data)
    items = [e for e in root.iter() if local(e.tag) in ("item", "entry")][:MAX_ITEMS_PER_FEED]
    out = []
    for it in items:
        link = child_link(it)
        if not link:
            continue
        out.append({
            "source": source,
            "title": clean(child_text(it, ("title",)), 200) or "無題",
            "description": clean(child_text(it, ("description", "summary", "encoded", "content"))) or "内容なし",
            "link": link,
            "ts": parse_date(child_text(it, ("pubDate", "published", "updated", "date"))),
        })
    return out


def build(feeds, outdir, page=None):
    articles, failed = [], []
    for name, url in feeds.items():
        try:
            items = parse_feed(name, fetch(url))
            if not items:
                raise ValueError("記事が0件")
            articles += items
            print(f"OK   {name}: {len(items)}件")
        except Exception as e:
            failed.append(name)
            print(f"FAIL {name}: {e}", file=sys.stderr)
    seen, unique = set(), []
    for a in sorted(articles, key=lambda a: a["ts"], reverse=True):
        if a["link"] not in seen:
            seen.add(a["link"])
            unique.append(a)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "news.json").write_text(
        json.dumps({"updated": int(time.time() * 1000), "failed": failed, "articles": unique}, ensure_ascii=False),
        encoding="utf-8",
    )
    if page and Path(page).exists():
        shutil.copy(page, outdir / "index.html")
    print(f"合計 {len(unique)}件 / 取得できなかった媒体 {len(failed)}件")
    return len(unique), failed


if __name__ == "__main__":
    count, failed = build(FEEDS, "site", "index.html")
    if count == 0:      # 1件も取れなかったときは、古い公開内容を壊さないように、失敗として終わる
        sys.exit(1)
