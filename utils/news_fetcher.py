# utils/news_fetcher.py
from __future__ import annotations
import html, re, asyncio
from typing import List, Dict, Optional
from xml.etree import ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

import httpx

# --- Набір джерел за замовчуванням (швидкі та надійні) ---
DEFAULT_FEEDS = [
    # Крипто (найшвидші)
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
    # Фінансові
    "https://www.cnbc.com/id/10001147/device/rss/rss.html",       # CNBC Markets
]

# Google News RSS: безкоштовний спосіб робити пошук по темі
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={ceid}"

HTTP_TIMEOUT = 5.0  # Зменшено для швидкості
MAX_PER_FEED = 8

async def _http_get_async(url: str, client: httpx.AsyncClient) -> Optional[str]:
    """Асинхронне завантаження URL."""
    try:
        r = await client.get(url, follow_redirects=True)
        if r.status_code == 200 and r.text:
            return r.text
    except Exception:
        pass
    return None

def _http_get(url: str, timeout: float = HTTP_TIMEOUT) -> Optional[str]:
    """Синхронне завантаження (fallback)."""
    try:
        with httpx.Client(timeout=timeout, headers={"User-Agent": "crypto-analyst-bot/1.0"}) as c:
            r = c.get(url, follow_redirects=True)
            if r.status_code == 200 and r.text:
                return r.text
    except Exception:
        pass
    return None

def _find_text(node: Optional[ET.Element], names: List[str]) -> str:
    if node is None:
        return ""
    for n in names:
        e = node.find(n)
        if e is not None and (e.text or "").strip():
            return e.text.strip()
    return ""

def _parse_date(s: str) -> float:
    try:
        dt = parsedate_to_datetime(s)
        return dt.timestamp()
    except Exception:
        return 0.0

def _parse_rss(xml_text: str) -> List[Dict]:
    out: List[Dict] = []
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return out

    # RSS 2.0
    for item in root.findall(".//item"):
        title = _find_text(item, ["title"])
        link  = _find_text(item, ["link"])
        pub   = _find_text(item, ["pubDate", "updated", "dc:date"])
        if title and link:
            out.append({"title": title, "link": link, "pub": pub, "ts": _parse_date(pub)})
        if len(out) >= MAX_PER_FEED:
            break

    # Atom (fallback)
    if not out:
        ns = "{http://www.w3.org/2005/Atom}"
        for entry in root.findall(f".//{ns}entry"):
            title = _find_text(entry, [f"{ns}title"])
            link_el = entry.find(f"{ns}link")
            link = link_el.get("href") if link_el is not None else ""
            pub = _find_text(entry, [f"{ns}updated", f"{ns}published"])
            if title and link:
                out.append({"title": title, "link": link, "pub": pub, "ts": _parse_date(pub)})
            if len(out) >= MAX_PER_FEED:
                break
    return out

def _md_esc(s: str) -> str:
    # Легкий Markdown-escape під Telegram
    return (
        s.replace("\\", "\\\\")
         .replace("_", "\\_")
         .replace("*", "\\*")
         .replace("[", "\\[")
         .replace("`", "\\`")
    )

def _short(s: str, n: int = 160) -> str:
    s = html.unescape((s or "").strip())
    s = re.sub(r"\s+", " ", s)
    return s if len(s) <= n else (s[: n - 1] + "…")

def _google_news_rss_query(q: str, lang: str) -> str:
    # lang: 'uk'/'en', регіон підбираємо відповідно
    if (lang or "").lower().startswith("uk"):
        return GOOGLE_NEWS_RSS.format(q=httpx.utils.quote(q), hl="uk", gl="UA", ceid="UA:uk")
    # дефолт ENG
    return GOOGLE_NEWS_RSS.format(q=httpx.utils.quote(q), hl="en", gl="US", ceid="US:en")


def _extract_source_name(url: str) -> str:
    """Витягує красиву назву джерела з URL."""
    source_map = {
        "coindesk": "CoinDesk",
        "cointelegraph": "Cointelegraph",
        "decrypt": "Decrypt",
        "theblock": "The Block",
        "cnbc": "CNBC",
        "reuters": "Reuters",
        "investing": "Investing.com",
        "yahoo": "Yahoo Finance",
        "binance": "Binance",
        "google": "Google News",
    }
    url_lower = url.lower()
    for key, name in source_map.items():
        if key in url_lower:
            return name
    return "News"


def _time_ago(ts: float) -> str:
    """Повертає 'X год тому' або 'X хв тому'."""
    if ts <= 0:
        return ""
    now = datetime.now(timezone.utc).timestamp()
    diff = now - ts
    if diff < 0:
        diff = 0
    if diff < 3600:  # менше години
        mins = int(diff / 60)
        return f"{mins} хв" if mins > 0 else "щойно"
    elif diff < 86400:  # менше доби
        hours = int(diff / 3600)
        return f"{hours} год"
    else:
        days = int(diff / 86400)
        return f"{days} дн"


async def get_latest_news_async(query: Optional[str] = None, max_items: int = 8, lang: str = "uk") -> List[Dict]:
    """
    АСИНХРОННА версія - завантажує всі фіди паралельно.
    Повертає список новин [{"title","link","source","ts","time_ago"}]
    """
    items: List[Dict] = []
    feeds_to_fetch: List[str] = []

    if query:
        q = query.strip()
        # Google News - основне джерело для пошуку
        feeds_to_fetch.append(_google_news_rss_query(q, lang))
        
        # Тематичні фіди
        q_low = q.lower()
        if any(k in q_low for k in ["bitcoin", "btc", "ethereum", "eth", "crypto", "крипто"]):
            feeds_to_fetch += [
                "https://www.coindesk.com/arc/outboundfeeds/rss/",
                "https://cointelegraph.com/rss",
            ]
    else:
        feeds_to_fetch = DEFAULT_FEEDS.copy()

    # Паралельне завантаження всіх фідів
    async with httpx.AsyncClient(
        timeout=HTTP_TIMEOUT, 
        headers={"User-Agent": "crypto-analyst-bot/1.0"}
    ) as client:
        tasks = [_http_get_async(url, client) for url in feeds_to_fetch]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    # Парсимо результати
    for url, xml in zip(feeds_to_fetch, results):
        if isinstance(xml, Exception) or not xml:
            continue
        source_name = _extract_source_name(url)
        for it in _parse_rss(xml):
            items.append({
                "title": _short(it["title"], 120),
                "link": it["link"],
                "source": source_name,
                "ts": it.get("ts", 0.0),
                "time_ago": _time_ago(it.get("ts", 0.0)),
            })

    # Сортуємо за часом і дедуплікуємо
    items.sort(key=lambda d: d.get("ts", 0.0), reverse=True)
    seen = set()
    uniq: List[Dict] = []
    for it in items:
        # Нормалізуємо title для порівняння
        title_norm = it["title"].lower().strip()[:50]
        if title_norm in seen:
            continue
        seen.add(title_norm)
        uniq.append(it)
        if len(uniq) >= max_items:
            break
    return uniq


def get_latest_news(query: Optional[str] = None, max_items: int = 8, lang: str = "uk") -> List[Dict]:
    """
    Повертає список новин [{"title","title_md","link","source","ts"}]
    - Якщо query задано → шукає через Google News RSS (плюс пару явних фідів для тем GOLD/USD/EUR/crypto)
    - Якщо query немає → збирає стрічки з DEFAULT_FEEDS
    """
    items: List[Dict] = []

    if query:
        q = query.strip()
        # Google News
        url = _google_news_rss_query(q, lang)
        xml = _http_get(url)
        if xml:
            for it in _parse_rss(xml):
                items.append({
                    "title": it["title"],
                    "title_md": _md_esc(_short(it["title"])),
                    "link": it["link"],
                    "source": "GoogleNews",
                    "ts": it.get("ts", 0.0)
                })

        # Тематичні фіди, якщо шукаємо конкретні macro-теми
        topic_feeds = []
        q_low = q.lower()
        if any(k in q_low for k in ["gold", "xau", "золото"]):
            topic_feeds += ["https://www.investing.com/rss/commodities.rss"]
        if any(k in q_low for k in ["dollar", "usd", "долар"]):
            topic_feeds += ["https://www.reuters.com/markets/currencies/rss"]  # FX з Reuters
        if any(k in q_low for k in ["euro", "eur", "євро"]):
            topic_feeds += ["https://www.reuters.com/markets/currencies/rss"]
        if any(k in q_low for k in ["bitcoin", "btc", "ethereum", "eth", "crypto", "крипто"]):
            topic_feeds += [
                "https://www.coindesk.com/arc/outboundfeeds/rss/",
                "https://cointelegraph.com/rss",
                "https://decrypt.co/feed",
                "https://www.theblock.co/rss",
            ]

        for f in topic_feeds:
            xml = _http_get(f)
            if not xml:
                continue
            for it in _parse_rss(xml):
                items.append({
                    "title": it["title"],
                    "title_md": _md_esc(_short(it["title"])),
                    "link": it["link"],
                    "source": f,
                    "ts": it.get("ts", 0.0)
                })

    else:
        # Без запиту — агрегуємо стандартні фіди
        for f in DEFAULT_FEEDS:
            xml = _http_get(f)
            if not xml:
                continue
            for it in _parse_rss(xml):
                items.append({
                    "title": it["title"],
                    "title_md": _md_esc(_short(it["title"])),
                    "link": it["link"],
                    "source": f,
                    "ts": it.get("ts", 0.0)
                })

    # Сортуємо за часом і обрізаємо
    items.sort(key=lambda d: d.get("ts", 0.0), reverse=True)
    # Дедуп по title+link
    seen = set()
    uniq: List[Dict] = []
    for it in items:
        key = (it["title"], it["link"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)
        if len(uniq) >= max_items:
            break
    return uniq
