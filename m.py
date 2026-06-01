#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🎯 KGS Local API Server - ALL ENDPOINTS
📡 Working Endpoints:
   • GET /api/batches                    → All batches list
   • GET /api/batch-meta/<id>            → Batch metadata
   • GET /api/today/<batch_id>           → Today's classes
   • GET /api/classroom/<batch_id>       → Classroom topics
   • GET /api/updates/<batch_id>         → Updates/announcements
   • GET /api/timetable/<batch_id>       → Timetable image
   • GET /api/batch-test-series/<id>     → Test series quizzes
   • GET /api/lesson/<lesson_id>         → Lesson details (videos/PDFs)
✅ Handles: zstd compression + sunny-keys seeding + cookie auth + caching
🔇 Silent mode: No VS Code side-panel clutter
"""

from flask import Flask, jsonify, request, Response
import requests
import json
import os
import sys
import time
import zstandard as zstd
from functools import wraps

# ================== CONFIG ==================
REMOTE_BASE = "https://kgs-web.vercel.app"
API_KEYS = "/api/sunny-keys"
CACHE_TTL = 3600  # Cache valid for 1 hour (seconds)
OUTPUT_DIR = "api_cache"

# 🍪 Session cookies (UPDATE if 403 - get fresh from DevTools)
COOKIES = {
    "sunny_a": "1780312658.1-AqTNVHpRboE53Qc7zHnA",
    "sunny_b": "1a5145763616d5e8d7c3e40967f4c40d7c17ff4d7b555e13e58b7f126f5d243d",
    "user_id": "a6428e2a-9c4d-4a97-b613-4456fac3830b",
    "session": "eyJfcGVybWFuZW50Ijp0cnVlLCJjc3JmX3Rva2VuIjoiSm1oVHdGMnVwX3VsRTJNcm9OamRNeEN6OWtVNTh5WkpEeG9Kb2ZmOU11MCIsInVzZXJfaWQiOiJhNjQyOGUyYS05YzRkLTRhOTctYjYxMy00NDU2ZmFjMzgzMGIifQ.ah1qUg.P8h3t3MvuE-2E0B6GGGtjff0ZKw"
}

HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "identity",  # Disable auto-encoding for manual zstd handling
    "Connection": "keep-alive",
    "Referer": f"{REMOTE_BASE}/batches",
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Android"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "X-Sunny-Req": "sunny"  # 🔑 Required by backend
}
# ============================================

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False
_cache = {}  # In-memory cache: {endpoint_key: {data, fetched_at}}
os.makedirs(OUTPUT_DIR, exist_ok=True)


def _log(msg):
    """Silent logger - only prints if DEBUG_API env var is set"""
    if os.environ.get("DEBUG_API"):
        print(f"[API] {msg}", file=sys.stderr)


def decompress_zstd(raw_bytes):
    """Decompress zstd-encoded response"""
    try:
        dctx = zstd.ZstdDecompressor()
        return dctx.decompress(raw_bytes)
    except Exception as e:
        _log(f"Zstd decompress failed: {e}")
        return None


def seed_sunny_keys(session, path, method="GET"):
    """Seed API keys via /api/sunny-keys (browser-mimicked flow)"""
    url = f"{REMOTE_BASE}{API_KEYS}"
    params = {"path": path, "method": method}
    try:
        resp = session.get(url, params=params, headers=HEADERS, cookies=COOKIES, timeout=20)
        if resp.status_code == 200:
            try:
                data = resp.json()
                ttl = data.get("t", 86400)
                _log(f"Keys seeded for {path} (TTL: {ttl//3600}h)")
                return True
            except:
                return True
    except Exception as e:
        _log(f"Seed keys error: {e}")
    return True


def _fetch_remote(endpoint_path, param_id=None):
    """Fetch data from remote API with compression handling"""
    session = requests.Session()
    session.cookies.update(COOKIES)
    
    # Build full path
    full_path = endpoint_path if param_id is None else f"{endpoint_path}/{param_id}"
    api_url = f"{REMOTE_BASE}{full_path}"
    
    try:
        # 🔑 Step 1: Seed keys first (required by backend)
        seed_sunny_keys(session, full_path, "GET")
        time.sleep(0.2)
        
        # 📡 Step 2: Fetch actual data
        _log(f"Fetching: {api_url}")
        resp = session.get(api_url, headers=HEADERS, timeout=30)
        
        if resp.status_code != 200:
            _log(f"Remote fetch failed: {resp.status_code}")
            return None
        
        # 🔓 Step 3: Handle compression (zstd/gzip)
        raw = resp.content
        encoding = resp.headers.get('Content-Encoding', '').lower()
        
        if encoding == 'zstd':
            decompressed = decompress_zstd(raw)
            if decompressed:
                raw = decompressed
        elif encoding in ['gzip', 'deflate']:
            import zlib
            raw = zlib.decompress(raw, 16+zlib.MAX_WBITS if encoding=='gzip' else -zlib.MAX_WBITS)
        
        # 📝 Step 4: Parse JSON
        return json.loads(raw.decode('utf-8'))
        
    except Exception as e:
        _log(f"Fetch error: {e}")
        return None


def _get_cached(endpoint_path, param_id=None, force=False):
    """Get data from cache or fetch fresh"""
    key = f"{endpoint_path}:{param_id}" if param_id else endpoint_path
    now = time.time()
    
    # Check in-memory cache
    if not force and key in _cache:
        if now - _cache[key]["fetched_at"] < CACHE_TTL:
            _log(f"Cache HIT: {key}")
            return _cache[key]["data"]
    
    # Check file cache
    safe_key = key.replace('/', '_').replace(':', '_')
    cache_file = os.path.join(OUTPUT_DIR, f"{safe_key}.json")
    
    if not force and os.path.exists(cache_file):
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                file_cache = json.load(f)
            if file_cache.get("_meta", {}).get("fetched_at", 0) + CACHE_TTL > now:
                _log(f"File cache HIT: {cache_file}")
                _cache[key] = {"data": file_cache["data"], "fetched_at": file_cache["_meta"]["fetched_at"]}
                return file_cache["data"]
        except:
            pass
    
    # Fetch fresh from remote
    _log(f"Cache MISS - fetching fresh: {key}")
    data = _fetch_remote(endpoint_path, param_id)
    
    if data:
        fetched_at = time.time()
        _cache[key] = {"data": data, "fetched_at": fetched_at}
        
        # Save to file cache
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump({"data": data, "_meta": {"fetched_at": fetched_at}}, f, indent=2, ensure_ascii=False)
        
        _log(f"Cached to: {cache_file}")
        return data
    
    # Fallback to stale cache if fetch fails
    if key in _cache:
        _log(f"Using stale cache: {key}")
        return _cache[key]["data"]
    
    return None


# ================== API ENDPOINTS ==================

@app.route('/api/batches', methods=['GET'])
def api_batches():
    """📦 GET /api/batches - All batches list"""
    data = _get_cached('/api/batches')
    if data is None:
        return jsonify({"error": "Failed to fetch batches"}), 502
    return jsonify(data)


@app.route('/api/batch-meta/<batch_id>', methods=['GET'])
def api_batch_meta(batch_id):
    """📋 GET /api/batch-meta/{id} - Batch metadata (overview, teachers)"""
    data = _get_cached('/api/batch-meta', batch_id)
    if data is None:
        return jsonify({"error": f"Failed to fetch batch-meta for {batch_id}"}), 502
    return jsonify(data)


@app.route('/api/today/<batch_id>', methods=['GET'])
def api_today(batch_id):
    """📅 GET /api/today/{batch_id} - Today's classes for batch"""
    data = _get_cached('/api/today', batch_id)
    if data is None:
        return jsonify({"error": f"Failed to fetch today's classes for batch {batch_id}"}), 502
    return jsonify(data)


@app.route('/api/classroom/<batch_id>', methods=['GET'])
def api_classroom(batch_id):
    """📚 GET /api/classroom/{batch_id} - Classroom topics for batch"""
    data = _get_cached('/api/classroom', batch_id)
    if data is None:
        return jsonify({"error": f"Failed to fetch classroom for batch {batch_id}"}), 502
    return jsonify(data)


@app.route('/api/updates/<batch_id>', methods=['GET'])
def api_updates(batch_id):
    """📢 GET /api/updates/{batch_id} - Updates/announcements for batch"""
    data = _get_cached('/api/updates', batch_id)
    if data is None:
        return jsonify({"error": f"Failed to fetch updates for batch {batch_id}"}), 502
    return jsonify(data)


@app.route('/api/timetable/<batch_id>', methods=['GET'])
def api_timetable(batch_id):
    """📆 GET /api/timetable/{batch_id} - Timetable image/data"""
    data = _get_cached('/api/timetable', batch_id)
    if data is None:
        return jsonify({"error": f"Failed to fetch timetable for batch {batch_id}"}), 502
    return jsonify(data)


@app.route('/api/batch-test-series/<batch_id>', methods=['GET'])
def api_test_series(batch_id):
    """📝 GET /api/batch-test-series/{id} - Test series quizzes"""
    data = _get_cached('/api/batch-test-series', batch_id)
    if data is None:
        return jsonify({"error": f"Failed to fetch test-series for batch {batch_id}"}), 502
    return jsonify(data)


@app.route('/api/lesson/<lesson_id>', methods=['GET'])
def api_lesson(lesson_id):
    """🎬 GET /api/lesson/{lesson_id} - Lesson details (videos, PDFs, notes)"""
    data = _get_cached('/api/lesson', lesson_id)
    if data is None:
        return jsonify({"error": f"Failed to fetch lesson {lesson_id}"}), 502
    return jsonify(data)

@app.route('/api/video/<video_id>', methods=['GET'])
def api_video(video_id):
    """🎬 GET /api/video/{video_id} - Video details (stream URL, PDFs, metadata)"""
    data = _get_cached('/api/video', video_id)
    if data is None:
        return jsonify({"error": f"Failed to fetch video {video_id}"}), 502
    return jsonify(data)

@app.route('/health', methods=['GET'])
def health():
    """🩺 Health check endpoint"""
    return jsonify({
        "status": "ok",
        "endpoints": [
            "/api/batches",
            "/api/batch-meta/<id>",
            "/api/today/<batch_id>",
            "/api/classroom/<batch_id>",
            "/api/updates/<batch_id>",
            "/api/timetable/<batch_id>",
            "/api/batch-test-series/<batch_id>",
            "/api/lesson/<lesson_id>",
            "/health",
            "/refresh/<endpoint>/<id>"
        ],
        "cache_dir": OUTPUT_DIR,
        "cache_ttl_seconds": CACHE_TTL
    })


@app.route('/refresh/<path:endpoint>', methods=['POST'])
def refresh_cache(endpoint):
    """🔄 Force refresh specific endpoint cache: POST /refresh/api/today/896"""
    # Parse endpoint like "api/today/896" → endpoint_path="/api/today", param_id="896"
    parts = endpoint.split('/')
    if len(parts) >= 3:
        endpoint_path = '/' + '/'.join(parts[:2])  # /api/today
        param_id = parts[2]  # 896
    else:
        endpoint_path = '/' + parts[0]  # /api/batches
        param_id = None
    
    _log(f"Manual refresh: {endpoint_path} + {param_id}")
    data = _fetch_remote(endpoint_path, param_id)
    
    if data:
        key = f"{endpoint_path}:{param_id}" if param_id else endpoint_path
        _cache[key] = {"data": data, "fetched_at": time.time()}
        safe_key = key.replace('/', '_').replace(':', '_')
        cache_file = os.path.join(OUTPUT_DIR, f"{safe_key}.json")
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump({"data": data, "_meta": {"fetched_at": _cache[key]["fetched_at"]}}, f, indent=2, ensure_ascii=False)
        return jsonify({"status": "refreshed", "endpoint": endpoint_path, "id": param_id})
    
    return jsonify({"error": "Refresh failed"}), 502


# ================== SERVER ==================

def _print_startup():
    """Print minimal startup info (VS Code friendly)"""
    print(f"✅ Local API Server: http://127.0.0.1:8000")
    print(f"📁 Cache: ./{OUTPUT_DIR}/")
    print(f"🔇 Silent: Set DEBUG_API=1 for verbose logs")


if __name__ == '__main__':
    # Pre-warm cache for most-used endpoint
    _get_cached('/api/batches')
    
    # Start server with minimal output
    _print_startup()
    
    # Run: host=127.0.0.1 for local-only, threaded for concurrent requests
    app.run(host='127.0.0.1', port=8000, debug=False, threaded=True)