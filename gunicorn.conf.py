# gunicorn.conf.py
import threading

# ── 워커 설정 ──
workers = 1          # 메모리 절약 (캐시 공유)
threads = 2          # 스레드 줄여서 메모리 절약
timeout = 300        # 5분 (API 수집 시간 충분히 확보)
graceful_timeout = 60
keepalive = 5
max_requests = 100          # 100요청마다 워커 재시작 (메모리 누수 방지)
max_requests_jitter = 20

# ── 서버 바인딩 ──
bind = "0.0.0.0:5000"

# ── 로깅 ──
loglevel = "info"
accesslog = "-"
errorlog  = "-"


# ── 서버 시작 시 백그라운드 데이터 수집 ──
def on_starting(server):
    """gunicorn 마스터 프로세스 시작 시 호출"""
    from app import refresh_cache
    import logging
    logging.getLogger("app").info("gunicorn 시작 — 백그라운드 데이터 수집 시작")
    threading.Thread(target=refresh_cache, daemon=True).start()
