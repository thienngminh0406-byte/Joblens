# gunicorn.conf.py
import threading

# ── 워커 설정 ──
workers = 1          # 메모리 절약 (캐시 공유)
threads = 2          # 스레드 줄여서 메모리 절약
timeout = 300        # 5분 (API 수집 시간 충분히 확보)
graceful_timeout = 60
keepalive = 5
max_requests = 500
max_requests_jitter = 50

# ── 서버 바인딩 ──
bind = "0.0.0.0:5000"

# ── 로깅 ──
loglevel = "info"
accesslog = "-"
errorlog  = "-"


# ── 서버 시작 시 백그라운드 데이터 수집 ──
def post_worker_init(worker):
    """워커 시작 후 캐시 초기화"""
    from app import refresh_cache
    import threading
    import logging

    logging.getLogger("app").info("Worker 시작 → CSV 캐시 초기화")

    threading.Thread(
        target=lambda: refresh_cache(csv_first=True),
        daemon=True
    ).start()
