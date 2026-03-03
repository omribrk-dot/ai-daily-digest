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
For each article below, write a 2-3 sentence summary IN HEBREW.
Capture the key news and why it matters. Clear, informative tone. No hype, no exclamation marks.

Output ONLY a JSON array:
[{{"index": 0, "summary": "..."}}, {{"index": 1, "summary": "..."}}, ...]

Articles:

{articles_text}"""


def _build_batch_text(articles: List[Article]) -> str:
    parts = []
    for i, a in enumerate(articles):
        parts.append(f"[{i}] Title: {a.title}\nSource: {a.source_name}\nContent: {a.content_snippet}\n")
    return "\n".join(parts)


def _parse_response(text: str, count: int) -> List[str]:
    # Try to extract JSON from response (handles code fences, preamble text)
    json_match = re.search(r"\[.*\]", text, re.DOTALL)
    if not json_match:
        logger.warning("No JSON array found in response")
        return [""] * count

    try:
        data = json.loads(json_match.group())
    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse error: {e}")
        return [""] * count

    summaries = [""] * count
    for item in data:
        idx = item.get("index", -1)
        if 0 <= idx < count:
            summaries[idx] = item.get("summary", "")
    return summaries


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
        summaries = None

        for attempt in range(3):
            try:
                response = client.messages.create(
                    model=model,
                    max_tokens=2000,
                    messages=[{"role": "user", "content": prompt}],
                )
                total_input_tokens += response.usage.input_tokens
                total_output_tokens += response.usage.output_tokens
                summaries = _parse_response(response.content[0].text, len(batch))
                break

            except anthropic.RateLimitError:
                wait = (attempt + 1) * 5
                logger.warning(f"Rate limited. Waiting {wait}s...")
                time.sleep(wait)

            except anthropic.APIError as e:
                logger.warning(f"API error (attempt {attempt + 1}/3): {e}")
                if attempt < 2:
                    time.sleep(2)

        if summaries is None:
            summaries = [""] * len(batch)

        for article, summary in zip(batch, summaries):
            article.summary_he = summary or "[סיכום לא זמין]"

        if i + batch_size < len(articles):
            time.sleep(delay)

    # Cost estimate (Sonnet pricing: $3/1M input, $15/1M output)
    cost = (total_input_tokens * 3 / 1_000_000) + (total_output_tokens * 15 / 1_000_000)
    logger.info(f"API usage: {total_input_tokens} input + {total_output_tokens} output tokens (~${cost:.3f})")

    return articles
