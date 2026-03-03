"""
Newsletter Splitter - Splits multi-topic newsletter articles into individual items.

Some newsletters (e.g. Bay Area Times) pack multiple unrelated news stories
into a single email. This module uses Claude to detect and split them into
separate Article objects so each gets its own card in the digest.
"""

import json
import logging
import re
from typing import List

import anthropic

from fetcher import Article, _make_id

logger = logging.getLogger(__name__)

SPLIT_PROMPT = """You are analyzing a newsletter email to extract individual news items.

The email below may contain MULTIPLE separate news stories. Your job is to identify each distinct news story and extract it.

Rules:
- Each news story should be about a DIFFERENT topic/company/event
- Skip promotional content, ads, and "read more" teasers with no substance
- Skip items that are just a headline with no real content
- Keep only items that have enough substance for a meaningful summary
- If the email contains only ONE topic, return just that one item

Output ONLY a JSON array:
[{{"index": 0, "title": "...", "content": "..."}}, ...]

Where:
- "title" = a clear, concise headline for this specific news item (in the original language)
- "content" = the relevant text/details for this item (2-4 sentences of source content)

Email subject: {subject}
Email source: {source}
Email content:
{content}"""


def split_articles(
    articles: List[Article],
    api_key: str,
    model: str = "claude-sonnet-4-6",
) -> List[Article]:
    """Split multi-topic newsletter articles into individual items.

    Only processes articles in the 'newsletters' category with enough content
    to potentially contain multiple topics (>300 chars).
    RSS articles and short emails pass through unchanged.
    """
    if not articles:
        return articles

    client = anthropic.Anthropic(api_key=api_key)
    result = []

    for article in articles:
        # Only split newsletter articles with substantial content
        if article.category != "newsletters" or len(article.content_snippet) < 300:
            result.append(article)
            continue

        logger.info(f"Splitting: {article.source_name} - {article.title[:50]}")

        prompt = SPLIT_PROMPT.format(
            subject=article.title,
            source=article.source_name,
            content=article.content_snippet,
        )

        try:
            response = client.messages.create(
                model=model,
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text

            json_match = re.search(r"\[.*\]", text, re.DOTALL)
            if not json_match:
                logger.warning(f"  No JSON in split response, keeping original")
                result.append(article)
                continue

            items = json.loads(json_match.group())

            if len(items) <= 1:
                # Single topic - keep original but update title if better
                if items and items[0].get("title"):
                    article.title = items[0]["title"]
                    article.content_snippet = items[0].get("content", article.content_snippet)
                result.append(article)
                logger.info(f"  Single topic, kept as-is")
            else:
                # Multiple topics - create separate articles
                for i, item in enumerate(items):
                    title = item.get("title", "").strip()
                    content = item.get("content", "").strip()
                    if not title or not content:
                        continue

                    split_id = _make_id(f"{article.id}:split:{i}")
                    result.append(Article(
                        id=split_id,
                        title=title,
                        url=article.url,
                        source_name=article.source_name,
                        category=article.category,
                        published=article.published,
                        content_snippet=content,
                    ))

                logger.info(f"  Split into {len(items)} items")

        except anthropic.RateLimitError:
            logger.warning(f"  Rate limited during split, keeping original")
            result.append(article)
        except Exception as e:
            logger.warning(f"  Split failed ({e}), keeping original")
            result.append(article)

    logger.info(f"Splitter: {len(articles)} articles -> {len(result)} articles")
    return result
