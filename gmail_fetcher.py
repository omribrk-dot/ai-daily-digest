"""
Gmail Fetcher - Fetch newsletters from a dedicated Gmail inbox via Gmail API.
"""

import base64
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from bs4 import BeautifulSoup

from fetcher import Article, _make_id

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CREDENTIALS_DIR = os.path.join(os.path.dirname(__file__), "credentials")
CLIENT_SECRET = os.path.join(CREDENTIALS_DIR, "client_secret.json")
TOKEN_FILE = os.path.join(CREDENTIALS_DIR, "token.json")


def _get_gmail_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CLIENT_SECRET):
                logger.error(
                    f"Gmail credentials not found at {CLIENT_SECRET}\n"
                    "To set up Gmail:\n"
                    "1. Go to console.cloud.google.com\n"
                    "2. Create project, enable Gmail API\n"
                    "3. Create OAuth2 credentials (Desktop App)\n"
                    "4. Download client_secret.json to credentials/\n"
                )
                return None
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def _extract_email_content(payload: dict) -> str:
    """Extract text content from Gmail message payload."""
    html_body = ""
    text_body = ""

    def _walk_parts(part):
        nonlocal html_body, text_body
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data", "")

        if mime == "text/html" and data:
            html_body = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        elif mime == "text/plain" and data:
            text_body = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

        for sub in part.get("parts", []):
            _walk_parts(sub)

    _walk_parts(payload)

    if html_body:
        soup = BeautifulSoup(html_body, "html.parser")
        # Remove tracking pixels, scripts, styles
        for tag in soup.find_all(["script", "style", "img"]):
            tag.decompose()
        # Remove unsubscribe / footer sections
        for tag in soup.find_all(string=re.compile(r"unsubscribe|view in browser|manage preferences", re.I)):
            parent = tag.find_parent(["div", "td", "p", "table"])
            if parent:
                parent.decompose()
        text = soup.get_text(separator=" ", strip=True)
    elif text_body:
        text = text_body
    else:
        text = ""

    return re.sub(r"\s+", " ", text).strip()[:3000]


def _get_header(headers: list, name: str) -> str:
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


SKIP_SUBJECT_PATTERNS = [
    r"new free subscriber",
    r"new paid subscriber",
    r"new comment on",
    r"someone liked your",
    r"new reply to",
    r"your post .* is live",
    r"stats for your post",
    r"welcome",
    r"confirm your",
    r"verify your",
    r"verification code",
    r"reset your password",
    r"sign[- ]?in",
    r"sign[- ]?up",
    r"login",
    r"security alert",
    r"growth tip",
    r"tip:",
    r"your .* is ready",
    r"invoice",
    r"receipt",
    r"payment",
    r"billing",
    r"shareable assets",
    r"share your post",
    r"your .* got .* likes",
    r"digest for",
]

_SKIP_RE = re.compile("|".join(SKIP_SUBJECT_PATTERNS), re.IGNORECASE)


def _is_noise(subject: str) -> bool:
    """Filter out system notifications that aren't actual newsletter content."""
    return bool(_SKIP_RE.search(subject))


def fetch_gmail(senders: List[dict], max_age_hours: int = 48) -> List[Article]:
    """Fetch newsletter emails from Gmail."""
    service = _get_gmail_service()
    if not service:
        return []

    after_date = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    after_str = after_date.strftime("%Y/%m/%d")

    # Build query - filter by senders if configured
    sender_emails = [s["email"] for s in senders if s.get("email")]
    if sender_emails:
        from_query = " OR ".join(f"from:{e}" for e in sender_emails)
        query = f"({from_query}) after:{after_str}"
    else:
        query = f"after:{after_str}"

    logger.info(f"Gmail query: {query}")

    try:
        results = service.users().messages().list(userId="me", q=query, maxResults=20).execute()
    except Exception as e:
        logger.error(f"Gmail API error: {e}")
        return []

    messages = results.get("messages", [])
    if not messages:
        logger.info("Gmail: no matching emails found")
        return []

    articles = []
    sender_names = {s["email"]: s["name"] for s in senders if s.get("email")}

    for msg_meta in messages:
        try:
            msg = service.users().messages().get(userId="me", id=msg_meta["id"], format="full").execute()
            headers = msg.get("payload", {}).get("headers", [])
            subject = _get_header(headers, "Subject")
            from_addr = _get_header(headers, "From")
            date_str = _get_header(headers, "Date")

            # Skip system notifications
            if _is_noise(subject or ""):
                logger.debug(f"  Skipping noise: {subject[:60]}")
                continue

            # Extract email address from "Name <email>" format
            email_match = re.search(r"<(.+?)>", from_addr)
            email_addr = email_match.group(1) if email_match else from_addr

            source_name = sender_names.get(email_addr, email_addr)

            # Parse date
            try:
                from email.utils import parsedate_to_datetime
                published = parsedate_to_datetime(date_str).astimezone(timezone.utc)
            except Exception:
                published = datetime.now(timezone.utc)

            content = _extract_email_content(msg.get("payload", {}))

            articles.append(Article(
                id=_make_id(f"gmail:{msg_meta['id']}"),
                title=subject.strip() if subject else "(no subject)",
                url=f"https://mail.google.com/mail/u/0/#inbox/{msg_meta['id']}",
                source_name=source_name,
                category="newsletters",
                published=published,
                content_snippet=content[:2000],
            ))

        except Exception as e:
            logger.warning(f"Failed to process email {msg_meta['id']}: {e}")
            continue

    logger.info(f"Gmail: {len(articles)} articles fetched")
    return articles


def discover_senders(max_age_hours: int = 168) -> dict:
    """List all unique senders from recent emails. Useful for first-time setup."""
    service = _get_gmail_service()
    if not service:
        return {}

    after_date = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    after_str = after_date.strftime("%Y/%m/%d")
    query = f"after:{after_str}"

    try:
        results = service.users().messages().list(userId="me", q=query, maxResults=100).execute()
    except Exception as e:
        logger.error(f"Gmail API error: {e}")
        return {}

    messages = results.get("messages", [])
    senders = {}

    for msg_meta in messages:
        try:
            msg = service.users().messages().get(
                userId="me", id=msg_meta["id"], format="metadata",
                metadataHeaders=["From", "Subject"]
            ).execute()
            headers = msg.get("payload", {}).get("headers", [])
            from_addr = _get_header(headers, "From")

            email_match = re.search(r"<(.+?)>", from_addr)
            email_addr = email_match.group(1) if email_match else from_addr
            display_name = from_addr.split("<")[0].strip().strip('"')

            if email_addr not in senders:
                senders[email_addr] = {"name": display_name, "count": 0}
            senders[email_addr]["count"] += 1

        except Exception:
            continue

    return senders
