"""
BIO → OPO 직항 모니터링 스크립트
- 대상: 2026-06-29, 빌바오(BIO) → 포르투(OPO), 15:00 이전 출발
- 데이터 소스: SerpApi Google Flights API
- 알림: Discord Webhook + ntfy

로컬 테스트 옵션:
  python check_flight.py              → 정상 실행 (상태 변화 있을 때만 알림)
  python check_flight.py --test       → API 조회만, 알림/상태저장 없음 (가장 먼저 실행)
  python check_flight.py --force      → 상태 무시하고 무조건 알림 발송 (알림 연동 확인용)
  python check_flight.py --mock-found → API 없이 직항 있는 척 mock 데이터로 전체 흐름 테스트
"""

import os
import sys
import json
import re
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── .env 로드 (로컬 전용, 없으면 무시) ───────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("[로컬] .env 파일 로드됨")
except ImportError:
    pass  # GitHub Actions 환경에선 dotenv 없어도 됨

# ── CLI 플래그 파싱 ───────────────────────────────────────────────────────────
_args = set(sys.argv[1:])
MODE_TEST       = "--test"       in _args  # API만 조회, 알림/저장 없음
MODE_FORCE      = "--force"      in _args  # 상태 무시하고 강제 알림
MODE_MOCK_FOUND = "--mock-found" in _args  # 직항 있는 척 mock 데이터

if MODE_TEST:       print("[모드] --test: API 조회만, 알림/상태저장 없음")
if MODE_FORCE:      print("[모드] --force: 상태 무시하고 강제 알림 발송")
if MODE_MOCK_FOUND: print("[모드] --mock-found: mock 데이터로 전체 흐름 테스트")

# ── 설정 ──────────────────────────────────────────────────────────────────────

CONFIG_FILE = Path(__file__).parent / "config.json"
STATE_FILE = Path(__file__).parent / "state.json"

# config.json 로드
try:
    config = json.loads(CONFIG_FILE.read_text())
    ORIGIN           = config["origin"]
    DESTINATION      = config["destination"]
    TARGET_DATE      = config["target_date"]
    DEPARTURE_BEFORE = config["departure_before"]
    FLIGHT_TYPE      = config.get("type", "2")       # 기본값: one-way
    MAX_STOPS        = config.get("stops", "1")      # 기본값: nonstop only
    DISCORD_ENABLED  = config.get("discord_enabled", True)  # 기본값: 활성화

    # outbound_times 자동 계산 (예: "15:00" → "0,14")
    departure_hour = int(DEPARTURE_BEFORE.split(":")[0])
    OUTBOUND_TIMES = f"0,{departure_hour - 1}"

except FileNotFoundError:
    print("[오류] config.json 파일이 없습니다.")
    sys.exit(1)
except (KeyError, json.JSONDecodeError, ValueError) as e:
    print(f"[오류] config.json 형식이 올바르지 않습니다: {e}")
    sys.exit(1)

# GitHub Secrets 또는 로컬 .env 에서 주입
SERPAPI_KEY           = os.environ.get("SERPAPI_KEY", "")
DISCORD_WEBHOOK_URL   = os.environ.get("DISCORD_WEBHOOK_URL", "")
NTFY_TOPIC            = os.environ.get("NTFY_TOPIC", "")


# ── 환경변수 검증 ─────────────────────────────────────────────────────────────

def validate_env() -> None:
    """필수 환경변수 누락 시 명확한 에러 출력"""
    missing = []

    # mock 모드면 SerpApi 키 없어도 됨
    if not MODE_MOCK_FOUND:
        if not SERPAPI_KEY: missing.append("SERPAPI_KEY")

    # test 모드면 알림 설정 없어도 됨
    if not MODE_TEST:
        if DISCORD_ENABLED and not DISCORD_WEBHOOK_URL: missing.append("DISCORD_WEBHOOK_URL")
        if not NTFY_TOPIC:                              missing.append("NTFY_TOPIC")

    if missing:
        print("\n[오류] 아래 환경변수가 설정되지 않았습니다:")
        for m in missing:
            print(f"  - {m}")
        print("\n.env 파일을 확인해주세요.\n")
        sys.exit(1)


# ── Mock 데이터 (--mock-found 테스트용) ──────────────────────────────────────

MOCK_FLIGHT_DATA = {
    "best_flights": [
        {
            "flights": [{
                "departure_airport": {"time": "10:30"},
                "arrival_airport": {"time": "12:10"},
                "airline": "Vueling",
                "flight_number": "VY 1234",
            }],
            "price": 89.99,
            "currency": "EUR",
        }
    ],
    "other_flights": []
}


# ── 항공편 조회 (SerpApi Google Flights) ─────────────────────────────────────

def fetch_direct_flights() -> list[dict]:
    """직항편 검색 (SerpApi Google Flights)"""
    resp = requests.get(
        "https://serpapi.com/search.json",
        params={
            "engine":         "google_flights",
            "departure_id":   ORIGIN,
            "arrival_id":     DESTINATION,
            "outbound_date":  TARGET_DATE,
            "type":           FLIGHT_TYPE,      # config.json에서 로드
            "stops":          MAX_STOPS,        # config.json에서 로드
            "outbound_times": OUTBOUND_TIMES,   # config.json departure_before에서 자동 계산
            "api_key":        SERPAPI_KEY,
        },
        timeout=15,
    )
    print(f"[SerpApi] 응답 코드: {resp.status_code}")

    if resp.status_code != 200:
        print(f"[SerpApi] API 호출 실패: {resp.status_code}")
        return []

    data = resp.json()

    # best_flights + other_flights 합치기
    all_flights = []
    all_flights.extend(data.get("best_flights", []))
    all_flights.extend(data.get("other_flights", []))

    # stops 설정에 따라 필터링
    if MAX_STOPS == "0":
        # any stops: 필터링 안 함
        filtered = all_flights
    elif MAX_STOPS == "1":
        # nonstop only: flights 길이 1
        filtered = [f for f in all_flights if len(f.get("flights", [])) == 1]
    elif MAX_STOPS == "2":
        # 1 stop max: flights 길이 <= 2
        filtered = [f for f in all_flights if len(f.get("flights", [])) <= 2]
    elif MAX_STOPS == "3":
        # 2 stops max: flights 길이 <= 3
        filtered = [f for f in all_flights if len(f.get("flights", [])) <= 3]
    else:
        filtered = all_flights

    print(f"[SerpApi] 조회된 항공편: {len(filtered)}편 (stops={MAX_STOPS})")
    return filtered


def filter_by_departure_time(offers: list[dict]) -> list[dict]:
    """departure_before 설정 기준 출발편 필터링 (SerpApi는 이미 필터링하지만 2차 검증)"""
    result = []
    for offer in offers:
        try:
            # SerpApi 응답: offer["flights"][0]["departure_airport"]["time"] = "10:30"
            dep_time = offer["flights"][0]["departure_airport"]["time"]
            if dep_time < DEPARTURE_BEFORE:
                result.append(offer)
        except (KeyError, IndexError, ValueError):
            continue
    print(f"[필터] {DEPARTURE_BEFORE} 이전 출발편: {len(result)}편")
    return result


def extract_flight_info(offer: dict) -> dict:
    flight = offer["flights"][0]
    return {
        "flight_number": flight.get("flight_number", "N/A"),
        "departure":     flight["departure_airport"]["time"],
        "arrival":       flight["arrival_airport"]["time"],
        "price":         str(offer.get("price", "N/A")),
        "currency":      offer.get("currency", "EUR"),
    }


# ── 상태 관리 ─────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_status": "UNKNOWN", "last_checked_at": None, "last_notified_at": None}


def save_state(status: str, notified: bool, state: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    state["last_status"]     = status
    state["last_checked_at"] = now
    if notified:
        state["last_notified_at"] = now
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    print(f"[상태] state.json 저장됨 → {status}")


# ── 알림 발송 ─────────────────────────────────────────────────────────────────

def notify_discord(title: str, message: str, is_found: bool) -> None:
    color = 0x00C851 if is_found else 0xFF4444
    payload = {
        "embeds": [{
            "title":       title,
            "description": message,
            "color":       color,
            "footer":      {"text": f"Flight Tracker • {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC"},
        }]
    }
    resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
    resp.raise_for_status()
    print("[Discord] 알림 전송 완료 ✓")


def notify_ntfy(title: str, message: str, is_found: bool) -> None:
    # ntfy Title 헤더는 ASCII만 지원 → 이모지 및 non-ASCII 문자 제거
    title_ascii = re.sub(r'[^\x00-\x7F]+', '', title).strip()

    resp = requests.post(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers={
            "Content-Type": "text/plain; charset=utf-8",
            "Title":        title_ascii,
            "Priority":     "high" if is_found else "default",
            "Tags":         "airplane" if is_found else "x",
        },
        timeout=10,
    )
    resp.raise_for_status()
    print("[ntfy] 알림 전송 완료 ✓")


def send_notifications(title: str, message: str, is_found: bool) -> None:
    errors = []
    if DISCORD_ENABLED:
        try:
            notify_discord(title, message, is_found)
        except Exception as e:
            errors.append(f"Discord 실패: {e}")
    else:
        print("[Discord] 비활성화됨 (config.json discord_enabled=false)")
    try:
        notify_ntfy(title, message, is_found)
    except Exception as e:
        errors.append(f"ntfy 실패: {e}")
    if errors:
        print("[경고] 일부 알림 실패:\n" + "\n".join(errors))


def build_found_message(filtered: list[dict]) -> tuple[str, str]:
    """직항이 새로 생겼을 때 알림 메시지"""
    lines = [
        f"**{f['flight_number']}** | {f['departure']} → {f['arrival']} | {f['price']} {f['currency']}"
        for f in [extract_flight_info(o) for o in filtered]
    ]
    return (
        f"[ROUTE OPEN] {ORIGIN} -> {DESTINATION} 직항 감지",
        f"🛫 **{ORIGIN} → {DESTINATION} 직항이 생겼습니다!**\n\n📅 날짜: {TARGET_DATE}\n⏰ {DEPARTURE_BEFORE} 이전 출발 편수: {len(filtered)}편\n\n" + "\n".join(lines),
    )


def build_closed_message() -> tuple[str, str]:
    """직항이 사라졌을 때 알림 메시지"""
    return (
        f"[ROUTE CLOSED] {ORIGIN} -> {DESTINATION} 직항 사라짐",
        f"{ORIGIN} → {DESTINATION} ({TARGET_DATE}) 직항이 더 이상 검색되지 않습니다.\n({DEPARTURE_BEFORE} 이전 출발 기준)",
    )


def build_not_found_message() -> tuple[str, str]:
    """직항이 없을 때 알림 메시지 (상태 체크용)"""
    return (
        f"[CHECK] {ORIGIN} -> {DESTINATION} 직항 없음",
        f"{ORIGIN} → {DESTINATION} ({TARGET_DATE}) 직항편이 검색되지 않습니다.\n({DEPARTURE_BEFORE} 이전 출발 기준)",
    )


# ── 메인 ──────────────────────────────────────────────────────────────────────

def main() -> None:
    now_kst = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M KST")
    print(f"\n{'='*50}")
    print(f"[{now_kst}] BIO→OPO 직항 확인 시작")
    print(f"{'='*50}")

    validate_env()

    # 1. 항공편 조회
    if MODE_MOCK_FOUND:
        print("[mock] 실제 API 호출 없이 mock 데이터 사용")
        # MOCK_FLIGHT_DATA에서 직항편 추출
        all_mock = []
        all_mock.extend(MOCK_FLIGHT_DATA.get("best_flights", []))
        all_mock.extend(MOCK_FLIGHT_DATA.get("other_flights", []))
        filtered = filter_by_departure_time(all_mock)
    else:
        try:
            offers   = fetch_direct_flights()
            filtered = filter_by_departure_time(offers)
        except Exception as e:
            print(f"[오류] API 호출 실패: {e}")
            return

    # 2. --test 모드: 조회 결과만 출력하고 종료
    if MODE_TEST:
        print(f"\n[--test 모드] 알림/상태저장 없이 종료")
        print(f"  결과: {'직항 있음 ✓' if filtered else '직항 없음'} ({len(filtered)}편)")
        return

    # 3. 상태 비교
    state          = load_state()
    prev_status    = state["last_status"]
    current_status = "FOUND" if filtered else "NOT_FOUND"

    print(f"\n  이전 상태: {prev_status}")
    print(f"  현재 상태: {current_status} ({len(filtered)}편)")

    # 4. 알림 발송 판단
    should_notify = (current_status != prev_status) or MODE_FORCE
    notified = False

    if should_notify:
        if MODE_FORCE:
            print("  [--force 모드] 상태 무관하게 알림 발송")

        # 3가지 케이스 분리
        if current_status == "FOUND":
            # 케이스 1: 직항이 생김
            title, message = build_found_message(filtered)
            is_found = True
        elif prev_status == "FOUND":
            # 케이스 2: 직항이 사라짐 (FOUND → NOT_FOUND)
            title, message = build_closed_message()
            is_found = False
        else:
            # 케이스 3: 직항 없음 (UNKNOWN/NOT_FOUND → NOT_FOUND)
            title, message = build_not_found_message()
            is_found = False

        send_notifications(title, message, is_found)
        notified = True
    else:
        print("  상태 변화 없음 → 알림 생략")

    # 5. 상태 저장
    save_state(current_status, notified, state)
    print(f"\n{'='*50}")
    print("  완료")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
