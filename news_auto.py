"""Automatic employment-news collector for ArabJobs.

Collects headlines from public RSS feeds, filters for employment/workforce relevance,
deduplicates them, and publishes short source-attributed entries to the existing
encrypted news store.  No article body is copied.

The collector is intentionally conservative: it publishes only headlines and a
short locally generated summary based on the feed metadata.  An optional
OPENROUTER_API_KEY can be configured later for higher-quality Arabic summaries.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_MINUTES = 30
MAX_ITEMS_PER_FEED = 12
MAX_PUBLISHED_PER_RUN = 10

DEFAULT_FEEDS = [
    "https://news.google.com/rss/search?q=employment+jobs+labor+market+recruitment&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=وظائف+توظيف+سوق+العمل&hl=ar&gl=SA&ceid=SA:ar",
    "https://news.google.com/rss/search?q=employment+Middle+East+jobs&hl=en-US&gl=AE&ceid=AE:en",
]

KEYWORDS = {
    "employment", "job", "jobs", "hiring", "recruitment", "recruiting",
    "workforce", "labor market", "labour market", "unemployment", "salary",
    "wage", "career", "vacancy", "vacancies", "layoff", "layoffs",
    "توظيف", "وظائف", "وظيفة", "عمل", "سوق العمل", "البطالة", "رواتب",
    "أجور", "توظيف", "موارد بشرية", "فرص عمل", "استقطاب", "تسريح"
}


def _feeds():
    raw = os.environ.get("NEWS_RSS_FEEDS", "").strip()
    if raw:
        return [x.strip() for x in raw.split("|") if x.strip()]
    return DEFAULT_FEEDS


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _relevance(title: str, description: str) -> int:
    hay = (title + " " + description).casefold()
    score = 0
    for keyword in KEYWORDS:
        if keyword.casefold() in hay:
            score += 12 if " " in keyword else 8
    if any(x in hay for x in ("job", "jobs", "employment", "توظيف", "وظائف", "سوق العمل")):
        score += 20
    return min(score, 100)


def _date(value: str) -> str:
    value = _normalize(value)
    if not value:
        return datetime.now(timezone.utc).isoformat()
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc).isoformat()
    except Exception:
        return value


def _source_from_link(link: str, fallback: str) -> str:
    host = re.sub(r"^https?://", "", str(link or "")).split("/", 1)[0]
    host = host.removeprefix("www.")
    return host or fallback


def _parse_feed(text: str, feed_url: str):
    root = ET.fromstring(text)
    items = []
    for node in root.findall(".//item")[:MAX_ITEMS_PER_FEED]:
        title = _normalize(node.findtext("title", ""))
        link = _normalize(node.findtext("link", ""))
        description = _normalize(node.findtext("description", ""))
        published = _date(node.findtext("pubDate", ""))
        if not title or not link:
            continue
        score = _relevance(title, description)
        if score < 35:
            continue
        items.append({
            "title": title,
            "link": link,
            "description": description,
            "publishedAt": published,
            "source": _source_from_link(link, feed_url),
            "relevance": score,
        })
    return items


def _fingerprint(item: dict) -> str:
    raw = (str(item.get("title", "")) + "|" + str(item.get("link", ""))).casefold()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _summary(item: dict) -> str:
    """Create a short original summary without copying article text."""
    source = _normalize(item.get("source", ""))
    return f"خبر متعلق بسوق العمل والتوظيف، وفقاً للمصدر {source}. لمعرفة التفاصيل الكاملة راجع المصدر الأصلي."


def _load_news(storage):
    try:
        return storage.load_news() or []
    except Exception:
        return []


def collect_and_publish(storage, logger_=logger):
    existing = _load_news(storage)
    if not isinstance(existing, list):
        existing = []
    known = set()
    for item in existing:
        if isinstance(item, dict):
            fp = item.get("autoFingerprint") or _fingerprint(item)
            known.add(str(fp))

    candidates = []
    for feed in _feeds():
        try:
            response = requests.get(
                feed,
                headers={"User-Agent": "ArabJobs-NewsBot/1.0"},
                timeout=15,
            )
            response.raise_for_status()
            candidates.extend(_parse_feed(response.text, feed))
        except Exception as exc:
            logger_.warning("Automatic news feed failed: %s", exc)

    candidates.sort(key=lambda x: (int(x.get("relevance", 0)), x.get("publishedAt", "")), reverse=True)
    created = []
    for item in candidates:
        fp = _fingerprint(item)
        if fp in known:
            continue
        news_id = max([int(x.get("id", 0)) for x in existing if isinstance(x, dict) and str(x.get("id", "")).isdigit()] + [0]) + 1
        record = {
            "id": news_id,
            "title": item["title"],
            "category": "سوق العمل",
            "content": _summary(item),
            "excerpt": _summary(item),
            "date": datetime.now().strftime("%Y-%m-%d"),
            "publishedAt": item["publishedAt"],
            "source": item["source"],
            "sourceUrl": item["link"],
            "status": "منشور",
            "image": "",
            "isAutomatic": True,
            "autoFingerprint": fp,
            "relevance": item["relevance"],
        }
        existing.append(record)
        known.add(fp)
        created.append(record)
        if len(created) >= MAX_PUBLISHED_PER_RUN:
            break

    if created and not storage.save_news(existing):
        return {"success": False, "created": 0, "message": "تعذر حفظ الأخبار الجديدة"}
    return {"success": True, "created": len(created), "items": created}


def register_news_automation(app, storage):
    """Register admin endpoints and start one background collector thread."""
    state = {"enabled": os.environ.get("NEWS_AUTO_PUBLISH", "1") != "0", "lastRun": None, "lastResult": None}
    interval = max(5, int(os.environ.get("NEWS_INTERVAL_MINUTES", str(DEFAULT_INTERVAL_MINUTES))))

    def run_once():
        result = collect_and_publish(storage)
        state["lastRun"] = datetime.now(timezone.utc).isoformat()
        state["lastResult"] = result
        logger.info("Automatic news run completed: created=%s", result.get("created", 0))
        return result

    @app.route("/api/admin/news/auto/status", methods=["GET"])
    def news_auto_status():
        from flask import jsonify, session
        users = storage.load_users() or []
        actor = next((u for u in users if str(u.get("id")) == str(session.get("user_id"))), None)
        if not actor or actor.get("role") != "admin":
            return jsonify({"success": False, "message": "غير مصرح"}), 401
        return jsonify({"success": True, "enabled": state["enabled"], "intervalMinutes": interval,
                        "lastRun": state["lastRun"], "lastResult": state["lastResult"]})

    @app.route("/api/admin/news/auto/run", methods=["POST"])
    def news_auto_run():
        from flask import jsonify, session
        users = storage.load_users() or []
        actor = next((u for u in users if str(u.get("id")) == str(session.get("user_id"))), None)
        if not actor or actor.get("role") != "admin":
            return jsonify({"success": False, "message": "غير مصرح"}), 401
        return jsonify(run_once())

    @app.route("/api/admin/news/auto/toggle", methods=["POST"])
    def news_auto_toggle():
        from flask import jsonify, session, request
        users = storage.load_users() or []
        actor = next((u for u in users if str(u.get("id")) == str(session.get("user_id"))), None)
        if not actor or actor.get("role") != "admin":
            return jsonify({"success": False, "message": "غير مصرح"}), 401
        payload = request.get_json(silent=True) or {}
        state["enabled"] = bool(payload.get("enabled", not state["enabled"]))
        return jsonify({"success": True, "enabled": state["enabled"]})

    def worker():
        # Wait once at startup so deployment does not immediately hammer RSS sources.
        time.sleep(20)
        while True:
            try:
                if state["enabled"]:
                    run_once()
            except Exception:
                logger.exception("Automatic news worker failed")
            time.sleep(interval * 60)

    thread = threading.Thread(target=worker, name="arabjobs-news-auto", daemon=True)
    thread.start()
    return state
