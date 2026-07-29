import json
import time
import threading
import requests
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ===== 설정 =====
BASE_URL = "https://www.law.go.kr"  # 링크가 상대경로이므로 도메인 붙여줌
INPUT_PATH = Path("Json_Files/DB01_law_parsed_ALL.json")
OUTPUT_PATH = Path("Json_Files/DB01_law_refined.json")  # 원하는 파일명으로 변경
TIMEOUT = 10
TEST_LIMIT = None  # 테스트로 앞 N건만 돌리고 싶으면 숫자 지정 (예: 5), 전체 실행은 None

MAX_WORKERS = 8       # 동시에 보낼 요청 수 (서버 부담 고려해 5~10 권장)
REQUEST_DELAY = 0.1   # 각 요청 사이 소폭 지연 (서버 차단 방지, 초 단위)

progress_lock = threading.Lock()
completed_count = 0


def build_json_url(detail_link: str) -> str:
    """법령상세링크의 type=HTML을 type=JSON으로 바꿔 완전한 URL 생성"""
    if detail_link.startswith("http"):
        url = detail_link
    else:
        url = BASE_URL + detail_link
    url = url.replace("type=HTML", "type=JSON")
    return url


def fetch_law_detail(detail_link: str):
    """법령상세링크로 실제 API 호출하여 JSON 응답 반환"""
    url = build_json_url(detail_link)
    time.sleep(REQUEST_DELAY)  # 요청 폭주 방지용 소폭 지연
    try:
        res = requests.get(url, timeout=TIMEOUT)
        res.raise_for_status()
        res.encoding = "utf-8"
        return res.json()
    except requests.exceptions.JSONDecodeError:
        print(f"⚠️ JSON 파싱 실패: {url}")
        return {"error": "invalid_json_response", "raw": res.text[:200]}
    except requests.exceptions.RequestException as e:
        print(f"⚠️ 요청 실패: {url} / {e}")
        return {"error": str(e)}


def process_item(idx_item, total):
    """개별 항목 하나를 처리 (스레드에서 실행됨)"""
    global completed_count
    idx, item = idx_item
    law_id = item.get("법령일련번호")
    detail_link = item.get("법령상세링크")

    if not detail_link:
        with progress_lock:
            completed_count += 1
            print(f"[{completed_count}/{total}] 링크 없음, 스킵: {law_id}")
        return None

    body = fetch_law_detail(detail_link)

    with progress_lock:
        completed_count += 1
        print(f"[{completed_count}/{total}] 완료: 법령일련번호={law_id}")

    return {
        "id": item.get("id", str(idx)),
        "법령일련번호": law_id,
        "본문": body
    }


def main():
    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 리스트가 아니라 dict로 감싸져 있는 경우 대비 (예: {"law": [...]})
    if isinstance(data, dict):
        for key in ("law", "items", "data"):
            if key in data and isinstance(data[key], list):
                data = data[key]
                break

    if TEST_LIMIT:
        data = data[:TEST_LIMIT]

    total = len(data)
    indexed_items = list(enumerate(data, start=1))

    result = []
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [
            executor.submit(process_item, idx_item, total)
            for idx_item in indexed_items
        ]
        for future in as_completed(futures):
            r = future.result()
            if r is not None:
                result.append(r)

    # id 기준으로 원래 순서 정렬 (병렬 처리로 완료 순서가 뒤섞이므로)
    result.sort(key=lambda x: int(x["id"]) if str(x["id"]).isdigit() else 0)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    elapsed = time.time() - start_time
    print(f"\n✅ 완료: {len(result)}건 저장 -> {OUTPUT_PATH}")
    print(f"⏱️ 소요 시간: {elapsed:.1f}초")


if __name__ == "__main__":
    main()