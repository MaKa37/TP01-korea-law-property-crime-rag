import json
import time
import threading
import requests
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ===== 설정 =====
BASE_URL = "https://www.law.go.kr"
INPUT_PATH = Path("Json_Files/DB10_admrul_parsed_ALL.json")
OUTPUT_PATH = Path("Json_Files/DB10_admrul_refined.json")
TIMEOUT = 10
TEST_LIMIT = None  # 테스트로 앞 N건만 돌리고 싶으면 숫자 지정 (예: 5), 전체 실행은 None

MAX_WORKERS = 8
REQUEST_DELAY = 0.1

# ===== DB10(행정규칙) 전용 필드명 =====
ID_FIELD = "행정규칙일련번호"
LINK_FIELD = "행정규칙상세링크"

progress_lock = threading.Lock()
completed_count = 0


def build_json_url(detail_link: str) -> str:
    """상세링크의 type=HTML을 type=JSON으로 바꿔 완전한 URL 생성"""
    if detail_link.startswith("http"):
        url = detail_link
    else:
        url = BASE_URL + detail_link
    url = url.replace("type=HTML", "type=JSON")
    return url


def fetch_detail(detail_link: str):
    """상세링크로 실제 API 호출하여 JSON 응답 반환"""
    url = build_json_url(detail_link)
    time.sleep(REQUEST_DELAY)
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
    item_id = item.get(ID_FIELD)
    detail_link = item.get(LINK_FIELD)

    if not detail_link:
        with progress_lock:
            completed_count += 1
            print(f"[{completed_count}/{total}] 링크 없음, 스킵: {item_id}")
        return None

    body = fetch_detail(detail_link)

    with progress_lock:
        completed_count += 1
        print(f"[{completed_count}/{total}] 완료: {ID_FIELD}={item_id}")

    return {
        "id": item.get("id", str(idx)),
        ID_FIELD: item_id,
        "본문": body
    }


def main():
    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        for key in ("law", "items", "data", "admrul"):
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

    result.sort(key=lambda x: int(x["id"]) if str(x["id"]).isdigit() else 0)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    elapsed = time.time() - start_time
    print(f"\n✅ 완료: {len(result)}건 저장 -> {OUTPUT_PATH}")
    print(f"⏱️ 소요 시간: {elapsed:.1f}초")


if __name__ == "__main__":
    main()