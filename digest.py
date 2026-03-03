#!/usr/bin/env python3
"""
AI Daily Digest - Main Orchestrator

Usage:
    python digest.py                     # Normal run
    python digest.py --dry-run           # Fetch only, no API calls
    python digest.py --no-open           # Don't open browser
    python digest.py --verbose           # Detailed logging
    python digest.py --discover-senders  # List Gmail senders
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import List

import yaml

from fetcher import Article, fetch_all_rss
from gmail_fetcher import fetch_gmail, discover_senders
from summarizer import summarize_articles
from renderer import render_digest

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(BASE_DIR, "cache", "seen.json")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
CONFIG_FILE = os.path.join(BASE_DIR, "config.yaml")

logger = logging.getLogger("digest")


# --- Cache ---

def load_cache() -> dict:
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, "r") as f:
            data = json.load(f)
            return data.get("articles", {})
    except (json.JSONDecodeError, KeyError):
        logger.warning("Corrupted cache file, starting fresh")
        return {}


def save_cache(seen: dict):
    # Prune entries older than 30 days
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    pruned = {k: v for k, v in seen.items() if v.get("first_seen", "") > cutoff}

    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump({
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "articles": pruned,
        }, f, ensure_ascii=False, indent=2)


def filter_seen(articles: List[Article], seen: dict) -> List[Article]:
    return [a for a in articles if a.id not in seen]


def mark_seen(articles: List[Article], seen: dict) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    for a in articles:
        if a.id not in seen:
            seen[a.id] = {
                "title": a.title[:80],
                "url": a.url,
                "first_seen": now,
            }
    return seen


# --- Config ---

def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        logger.error(f"Config file not found: {CONFIG_FILE}")
        sys.exit(1)
    with open(CONFIG_FILE, "r") as f:
        return yaml.safe_load(f)


# --- Main ---

def run_discover_senders():
    print("\nDiscovering Gmail senders (last 7 days)...")
    print("=" * 50)
    senders = discover_senders(max_age_hours=168)
    if not senders:
        print("No senders found (or Gmail not connected).")
        return

    sorted_senders = sorted(senders.items(), key=lambda x: x[1]["count"], reverse=True)
    print(f"\n{'Email':<40} {'Name':<25} {'Emails':<6}")
    print("-" * 71)
    for email, info in sorted_senders:
        print(f"{email:<40} {info['name']:<25} {info['count']:<6}")

    print(f"\nTotal: {len(sorted_senders)} unique senders")
    print("\nAdd the ones you want to config.yaml under sources.gmail.senders")


def main():
    parser = argparse.ArgumentParser(description="AI Daily Digest")
    parser.add_argument("--dry-run", action="store_true", help="Fetch only, skip API summarization")
    parser.add_argument("--no-open", action="store_true", help="Don't open browser after generation")
    parser.add_argument("--verbose", action="store_true", help="Detailed logging")
    parser.add_argument("--discover-senders", action="store_true", help="List Gmail senders")
    parser.add_argument("--no-gmail", action="store_true", help="Skip Gmail fetching")
    args = parser.parse_args()

    # Logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[logging.StreamHandler()],
    )

    # Discover senders mode
    if args.discover_senders:
        run_discover_senders()
        return

    print("\nAI Daily Digest")
    print("=" * 40)
    start_time = time.time()

    # Load config
    config = load_config()
    settings = config.get("settings", {})
    sources = config.get("sources", {})

    max_age = settings.get("max_age_hours", 48)
    max_per_source = settings.get("max_articles_per_source", 5)
    max_total = settings.get("max_total_articles", 50)

    # Fetch RSS
    rss_sources = sources.get("rss", [])
    all_articles = []

    if rss_sources:
        print(f"\nFetching {len(rss_sources)} RSS feeds...")
        rss_articles = fetch_all_rss(rss_sources, max_age, max_per_source)
        all_articles.extend(rss_articles)
        print(f"  RSS: {len(rss_articles)} articles")

    # Fetch Gmail
    gmail_config = sources.get("gmail", {})
    if gmail_config.get("enabled") and not args.no_gmail:
        senders = gmail_config.get("senders", [])
        if senders:
            print(f"\nFetching Gmail ({len(senders)} senders)...")
            try:
                gmail_articles = fetch_gmail(senders, max_age)
                all_articles.extend(gmail_articles)
                print(f"  Gmail: {len(gmail_articles)} articles")
            except Exception as e:
                logger.warning(f"  Gmail: skipped ({e})")

    if not all_articles:
        print("\nNo articles found. Check your sources in config.yaml")
        return

    # Dedup via cache
    seen = load_cache()
    new_articles = filter_seen(all_articles, seen)
    print(f"\nNew articles: {len(new_articles)} (filtered {len(all_articles) - len(new_articles)} seen)")

    if not new_articles:
        print("No new articles to process.")
        return

    # Apply total limit
    new_articles = new_articles[:max_total]

    # Summarize
    if not args.dry_run:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            logger.error(
                "ANTHROPIC_API_KEY not set.\n"
                "Add it to .env file: ANTHROPIC_API_KEY=sk-ant-..."
            )
            sys.exit(1)

        model = settings.get("model", "claude-sonnet-4-6")
        batch_size = settings.get("batch_size", 5)
        delay = settings.get("api_delay_seconds", 0.5)

        print(f"\nSummarizing {len(new_articles)} articles with {model}...")
        summarize_articles(new_articles, api_key, model, batch_size, delay)
    else:
        print("\n[DRY RUN] Skipping summarization")
        for a in new_articles:
            a.summary_he = "[dry run - ללא סיכום]"

    # Render HTML
    print("\nRendering HTML...")
    html = render_digest(new_articles, max_age)

    # Save
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    output_path = os.path.join(OUTPUT_DIR, f"{date_str}.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    # Update cache
    seen = mark_seen(new_articles, seen)
    save_cache(seen)

    elapsed = time.time() - start_time
    print(f"\nDone in {elapsed:.1f}s")
    print(f"Output: {output_path}")

    # Open in browser
    if not args.no_open and settings.get("open_in_browser", True) and not args.dry_run:
        subprocess.run(["open", output_path])


if __name__ == "__main__":
    main()
