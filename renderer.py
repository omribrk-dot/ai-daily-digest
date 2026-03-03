"""
HTML Renderer - Generates the digest page using Jinja2.
"""

import os
from collections import OrderedDict
from datetime import datetime, timezone
from typing import List

from jinja2 import Environment, FileSystemLoader

from fetcher import Article

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")

CATEGORY_DISPLAY = OrderedDict([
    ("newsletters", "ניוזלטרים"),
    ("websites", "אתרים וחדשות"),
    ("hebrew", "טק ישראלי"),
])

HEBREW_DAYS = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]
HEBREW_MONTHS = [
    "", "ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני",
    "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר",
]


def _hebrew_date(dt: datetime) -> str:
    day_name = HEBREW_DAYS[dt.weekday()]
    month_name = HEBREW_MONTHS[dt.month]
    return f"יום {day_name}, {dt.day} ב{month_name} {dt.year}"


def _time_ago(dt: datetime) -> str:
    now = datetime.now(timezone.utc)
    delta = now - dt
    total_seconds = delta.total_seconds()

    if total_seconds < 3600:
        minutes = max(1, int(total_seconds / 60))
        return f"לפני {minutes} דקות"
    elif total_seconds < 86400:
        hours = int(total_seconds / 3600)
        return f"לפני {hours} שעות"
    else:
        days = delta.days
        if days == 1:
            return "אתמול"
        return f"לפני {days} ימים"


def _group_articles(articles: List[Article]) -> OrderedDict:
    grouped = OrderedDict()

    for cat_key, cat_display in CATEGORY_DISPLAY.items():
        cat_articles = [a for a in articles if a.category == cat_key]
        if cat_articles:
            grouped[cat_display] = cat_articles

    # Any uncategorized
    known_cats = set(CATEGORY_DISPLAY.keys())
    other = [a for a in articles if a.category not in known_cats]
    if other:
        grouped["אחר"] = other

    return grouped


class ArticleWrapper:
    """Wraps Article to add template-friendly computed properties."""

    def __init__(self, article: Article):
        self._article = article
        self._category_display = ""

    def __getattr__(self, name):
        return getattr(self._article, name)

    @property
    def time_ago(self):
        return _time_ago(self._article.published)

    @property
    def category_display(self):
        return self._category_display


def render_digest(
    articles: List[Article],
    max_age_hours: int = 48,
) -> str:
    # Sort newest first for swipe UI
    sorted_articles = sorted(articles, key=lambda a: a.published, reverse=True)
    wrapped = [ArticleWrapper(a) for a in sorted_articles]

    # Map category keys to Hebrew display names
    cat_display = {k: v for k, v in CATEGORY_DISPLAY.items()}

    for w in wrapped:
        w._category_display = cat_display.get(w.category, w.category)

    source_names = set(a.source_name for a in articles)

    now = datetime.now(timezone.utc)
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))
    template = env.get_template("digest.html")

    return template.render(
        articles=wrapped,
        date_hebrew=_hebrew_date(now),
        total_articles=len(articles),
        total_sources=len(source_names),
        max_age_hours=max_age_hours,
        generation_time=now.strftime("%H:%M"),
    )
