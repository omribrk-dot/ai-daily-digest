"""
Claude API Summarizer - Batch summarization with Hebrew output.
"""

import json
import logging
import re
import time
from typing import List

import anthropic

from fetcher import Article

logger = logging.getLogger(__name__)

BATCH_PROMPT = """You are a tech news summarizer for an Israeli AI audience.
For each article below, produce THREE things IN HEBREW:

1. "title_he" - A clear, concise Hebrew headline for this news item.
2. "summary" - 2-3 sentence summary capturing the key news and why it matters.
3. "detail" - 1-2 sentences that DEEPEN the same topics from the summary. Add a specific number, quote, context, or implication that makes the summary richer. Do NOT introduce new topics - stay on the same story.

Clear, informative tone. No hype, no exclamation marks.

Output ONLY a JSON array:
[{{"index": 0, "title_he": "...", "summary": "...", "detail": "..."}}, ...]

Articles:

{articles_text}"""


def _build_batch_text(articles: List[Article]) -> str:
    parts = []
    for i, a in enumerate(articles):
        parts.append(f"[{i}] Title: {a.title}\nSource: {a.source_name}\nContent: {a.content_snippet}\n")
    return "\n".join(parts)


def _parse_response(text: str, count: int) -> List[dict]:
    # Try to extract JSON from response (handles code fences, preamble text)
    json_match = re.search(r"\[.*\]", text, re.DOTALL)
    if not json_match:
        logger.warning("No JSON array found in response")
        return [{"summary": "", "detail": "", "title_he": ""}] * count

    try:
        data = json.loads(json_match.group())
    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse error: {e}")
        return [{"summary": "", "detail": "", "title_he": ""}] * count

    results = [{"summary": "", "detail": "", "title_he": ""}] * count
    for item in data:
        idx = item.get("index", -1)
        if 0 <= idx < count:
            results[idx] = {
                "summary": item.get("summary", ""),
                "detail": item.get("detail", ""),
                "title_he": item.get("title_he", ""),
            }
    return results


def summarize_articles(
    articles: List[Article],
    api_key: str,
    model: str = "claude-sonnet-4-6",
    batch_size: int = 5,
    delay: float = 0.5,
) -> List[Article]:
    if not articles:
        return articles

    client = anthropic.Anthropic(api_key=api_key)
    total_input_tokens = 0
    total_output_tokens = 0

    for i in range(0, len(articles), batch_size):
        batch = articles[i : i + batch_size]
        batch_num = (i // batch_size) + 1
        total_batches = (len(articles) + batch_size - 1) // batch_size
        logger.info(f"Summarizing batch {batch_num}/{total_batches} ({len(batch)} articles)")

        prompt = BATCH_PROMPT.format(articles_text=_build_batch_text(batch))
        results = None

        for attempt in range(3):
            try:
                response = client.messages.create(
                    model=model,
                    max_tokens=4000,
                    messages=[{"role": "user", "content": prompt}],
                )
                total_input_tokens += response.usage.input_tokens
                total_output_tokens += response.usage.output_tokens
                results = _parse_response(response.content[0].text, len(batch))
                break

            except anthropic.RateLimitError:
                wait = (attempt + 1) * 5
                logger.warning(f"Rate limited. Waiting {wait}s...")
                time.sleep(wait)

            except anthropic.APIError as e:
                logger.warning(f"API error (attempt {attempt + 1}/3): {e}")
                if attempt < 2:
                    time.sleep(2)

        if results is None:
            results = [{"summary": "", "detail": ""}] * len(batch)

        for article, result in zip(batch, results):
            article.summary_he = result["summary"] or "[סיכום לא זמין]"
            article.detail_he = result.get("detail", "")
            if result.get("title_he"):
                article.title = result["title_he"]

        if i + batch_size < len(articles):
            time.sleep(delay)

    # Cost estimate (Sonnet pricing: $3/1M input, $15/1M output)
    cost = (total_input_tokens * 3 / 1_000_000) + (total_output_tokens * 15 / 1_000_000)
    logger.info(f"API usage: {total_input_tokens} input + {total_output_tokens} output tokens (~${cost:.3f})")

    return articles
