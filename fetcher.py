"""
RSS Feed Fetcher - Parallel fetching with error isolation.
"""

import hashlib
import html
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

import feedparser

logger = logging.getLogger(__name__)


@dataclass
class Article:
    id: str
    title: str
    url: str
    source_name: str
    category: str
    published: datetime
    content_snippet: str
    summary_he: str = ""


def _make_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def _strip_html(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parse_published(entry) -> Optional[datetime]:
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            try:
                from time import mktime
                return datetime.fromtimestamp(mktime(parsed), tz=timezone.utc)
            except (ValueError, OverflowError):
                continue
    return None


def _get_content(entry) -> str:
    if hasattr(entry, "content") and entry.content:
        raw = entry.content[0].get("value", "")
    elif hasattr(entry, "summary"):
        raw = entry.summary or ""
    else:
        raw = ""
    return _strip_html(raw)[:500]


def _fetch_single_feed(source: dict, max_age_hours: int, max_articles: int) -> List[Article]:
    name = source["name"]
    url = source["url"]
    category = source.get("category", "websites")

    logger.info(f"Fetching: {name}")

    try:
        feed = feedparser.parse(url)
    except Exception as e:
        logger.warning(f"Failed to parse {name}: {e}")
        return []

    if feed.bozo and not feed.entries:
        logger.warning(f"Malformed feed with no entries: {name}")
        return []

    now = datetime.now(timezone.utc)
    articles = []

    for entry in feed.entries:
        link = getattr(entry, "link", None)
        title = getattr(entry, "title", None)
        if not link or not title:
            continue

        published = _parse_published(entry)
        if not published:
            published = now

        age_hours = (now - published).total_seconds() / 3600
        if age_hours > max_age_hours:
            continue

        articles.append(Article(
            id=_make_id(link),
            title=title.strip(),
            url=link.strip(),
            source_name=name,
            category=category,
            published=published,
            content_snippet=_get_content(entry),
        ))

    articles.sort(key=lambda a: a.published, reverse=True)
    return articles[:max_articles]


def fetch_all_rss(sources: List[dict], max_age_hours: int = 48, max_articles_per_source: int = 5) -> List[Article]:
    all_articles = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(_fetch_single_feed, src, max_age_hours, max_articles_per_source): src
            for src in sources
        }
        for future in as_completed(futures):
            src = futures[future]
            try:
                articles = future.result()
                logger.info(f"  {src['name']}: {len(articles)} articles")
                all_articles.extend(articles)
            except Exception as e:
                logger.warning(f"  {src['name']}: error - {e}")

    all_articles.sort(key=lambda a: a.published, reverse=True)
    return all_articles
