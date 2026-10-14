"""Comunicados oficiales: contexto macro, nunca votos de compra/venta."""

import asyncio
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from .market_data import client

FED_RSS = 'https://www.federalreserve.gov/feeds/press_monetary.xml'
_cache: tuple[float, list[dict]] | None = None
_lock = asyncio.Lock()


def parse_feed(xml: str, now: float) -> list[dict]:
    out, seen = [], set()
    for item in ET.fromstring(xml).findall('./channel/item'):
        url = item.findtext('link') or ''
        try:
            stamp = int(parsedate_to_datetime(item.findtext('pubDate') or '').timestamp())
        except (ValueError, TypeError, OverflowError):
            continue
        if not url.startswith('https://www.federalreserve.gov/') or url in seen or not 0 <= now - stamp <= 30 * 86400:
            continue
        seen.add(url)
        out.append(dict(user='Federal Reserve', avatar=None, text=(item.findtext('title') or '')[:400],
                        badge='neutral', likes=None, time=stamp, url=url, image=None))
    return sorted(out, key=lambda i: i['time'], reverse=True)[:20]


async def official_news() -> dict:
    global _cache
    async with _lock:
        now = time.time()
        if not _cache or now - _cache[0] >= 300:
            response = await client().get(FED_RSS)
            response.raise_for_status()
            _cache = (time.time(), parse_feed(response.text, time.time()))
        items = [i for i in _cache[1] if 0 <= now - i['time'] <= 30 * 86400]
    return dict(source='Federal Reserve · política monetaria', detail=f'{len(items)} comunicados recientes; contexto sin voto direccional',
                bullish=.5, sample=0, items=items)
