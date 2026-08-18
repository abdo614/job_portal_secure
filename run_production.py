#!/usr/bin/env python
"""
Production Server Startup Script
تشغيل الخادم الإنتاجي باستخدام Waitress WSGI Server
"""
import os
from waitress import serve
from app import app

if __name__ == '__main__':
    # Production configuration
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', 61411))
    threads = int(os.environ.get('THREADS', 4))

    print(f"Starting production server on {host}:{port}")
    print(f"Threads: {threads}")
    print("Press Ctrl+C to stop")

    # Start Waitress WSGI server
    serve(
        app,
        host=host,
        port=port,
        threads=threads,
        url_scheme='https' if os.environ.get('FLASK_HTTPS') == '1' else 'http'
    )