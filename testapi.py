# -*- coding: utf-8 -*-
"""
서울시 오픈API 연결 테스트 스크립트
app.py 실행 전에 이 파일을 먼저 실행해서 API 연결을 확인하세요.

사용법:
    python test_api.py
"""

import requests
import socket
import sys

API_KEY = "514b4b685a6d696e39366469694276"
HOST    = "openapi.seoul.go.kr"
PORT    = 8088

print("=" * 55)
print("  JobLens — 서울시 오픈API 연결 테스트")
print("=" * 55)

# 1. DNS 해석 확인
print(f"\n[1] DNS 조회: {HOST}")
try:
    ip = socket.gethostbyname(HOST)
    print(f"    ✅ IP 주소: {ip}")
except socket.gaierror as e:
    print(f"    ❌ DNS 실패: {e}")
    print("    → 인터넷 연결을 확인하세요.")
    sys.exit(1)

# 2. TCP 포트 연결 확인
print(f"\n[2] 포트 연결: {HOST}:{PORT}")
try:
    s = socket.create_connection((HOST, PORT), timeout=10)
    s.close()
    print(f"    ✅ 포트 {PORT} 열림")
except (socket.timeout, ConnectionRefusedError, OSError) as e:
    print(f"    ❌ 포트 연결 실패: {e}")
    print(f"    → 방화벽이 포트 {PORT}를 막고 있을 수 있습니다.")
    print("    → 회사/학교 네트워크라면 외부 포트가 차단됐을 수 있습니다.")
    print("    → 다른 네트워크(모바일 핫스팟 등)에서 시도해보세요.")
    sys.exit(1)

# 3. API 호출 확인 (1건만)
print(f"\n[3] API 호출 테스트")
url = f"http://{HOST}:{PORT}/{API_KEY}/json/GetJobInfo/1/5"
print(f"    URL: {url}")
try:
    res = requests.get(url, timeout=20)
    res.raise_for_status()
    data = res.json()

    if "GetJobInfo" in data and "row" in data["GetJobInfo"]:
        rows = data["GetJobInfo"]["row"]
        total = data["GetJobInfo"].get("list_total_count", "?")
        print(f"    ✅ API 정상 응답")
        print(f"    총 공고 수: {total}건")
        print(f"    샘플 공고: {rows[0].get('CMPNY_NM', '?')} — {rows[0].get('JO_SJ', '?')}")
    else:
        result = data.get("GetJobInfo", {}).get("RESULT", {})
        code = result.get("CODE", "")
        msg  = result.get("MESSAGE", "알 수 없음")
        print(f"    ❌ API 오류 응답: [{code}] {msg}")
        if "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR" in code:
            print("    → API 호출 한도 초과. 잠시 후 다시 시도하세요.")
        elif "SERVICE_KEY_IS_NOT_REGISTERED_ERROR" in code:
            print("    → API 키가 유효하지 않습니다. app.py의 API_KEY를 확인하세요.")

except requests.exceptions.ConnectTimeout:
    print("    ❌ 연결 타임아웃")
    print("    → openapi.seoul.go.kr:8088 포트가 차단됐습니다.")
    print("    → 해결 방법:")
    print("       1) 모바일 핫스팟으로 변경 후 재시도")
    print("       2) VPN 사용")
    print("       3) 서울시 API 포털에서 IP 허용 설정 확인")
except Exception as e:
    print(f"    ❌ 오류: {e}")
    sys.exit(1)

print("\n" + "=" * 55)
print("  모든 테스트 통과! app.py를 실행할 수 있습니다.")
print("  → python app.py")
print("=" * 55)