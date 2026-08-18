#!/usr/bin/env python
"""
Production Server Startup Script
تشغيل الخادم الإنتاجي باستخدام Waitress WSGI Server
"""
import os
from waitress import serve
from app import app
from encryption import secure_storage
from news_auto import register_news_automation

# تشغيل مجمّع الأخبار التلقائي مرة واحدة لكل عملية Waitress.
# الإعدادات الافتراضية: تشغيل النشر التلقائي كل 30 دقيقة.
# يمكن إيقافه مؤقتاً عبر NEWS_AUTO_PUBLISH=0.
news_automation_state = register_news_automation(app, secure_storage)

if __name__ == '__main__':
    # Production configuration
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', 61411))
    threads = int(os.environ.get('THREADS', 4))

    print(f"Starting production server on {host}:{port}")
    print(f"Threads: {threads}")
    print(f"Automatic news publishing: {'ON' if news_automation_state.get('enabled') else 'OFF'}")
    print(f"Automatic news interval: {news_automation_state.get('intervalMinutes')} minutes")
    print("Press Ctrl+C to stop")

    # Start Waitress WSGI server
    serve(
        app,
        host=host,
        port=port,
        threads=threads,
        url_scheme='https' if os.environ.get('FLASK_HTTPS') == '1' else 'http'
    )
