"""Safe smoke test for the automatic news collector.

Does not write application data or publish anything. It validates that the
collector module imports and that its core configuration is present.
"""
import news_auto


def main():
    required = [
        "fetch_feed",
        "collect_news",
        "register_news_automation",
    ]
    missing = [name for name in required if not hasattr(news_auto, name)]
    if missing:
        raise SystemExit(f"Missing news collector functions: {', '.join(missing)}")
    print("news_auto smoke test: OK")


if __name__ == "__main__":
    main()
