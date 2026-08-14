"""격리 테스트 2단계: 우리 서버 -> 클라이언트 구간 + 클라이언트 읽기 방식 테스트.

1단계(diag_stream_direct.py)가 깨끗했다면, 이제 실제 /chat API를
STREAM_MODE=realtime으로 띄운 상태에서 호출하되, 클라이언트가 응답을
읽는 방식을 두 가지로 비교한다:

  A) resp.iter_lines(decode_unicode=True) - 지금까지 테스트에 쓰던 방식
  B) resp.content 전체를 한 번에 받은 뒤 .decode('utf-8') - 청크 단위
     디코딩을 아예 안 거치고, 완성된 바이트 전체를 한 번에 디코딩

A에서만 깨지고 B는 깨끗하다면 -> "클라이언트가 청크 단위로 나눠 읽는
방식" 자체가 범인(실제 브라우저의 EventSource/fetch는 이런 식으로
안 읽으므로, 실사용에는 영향 없는 "테스트 스크립트 한정 버그"일 수 있음).
A와 B 둘 다 깨진다면 -> 우리 서버가 클라이언트로 보내는 바이트 자체가
이미 손상된 것이므로, 서버 쪽(SSE 릴레이/청크 전송)에 진짜 문제가 있음.

⚠️ 실행 전 서버를 STREAM_MODE=realtime으로 띄워야 한다:
    STREAM_MODE=realtime uvicorn app.main:app --reload

사용법:
    python scripts/diag_stream_client.py "질문 텍스트"
    python scripts/diag_stream_client.py "질문 텍스트" --repeat 5
"""
import argparse
import json
import os

import requests

API_URL = os.getenv("DIAG_API_URL", "http://127.0.0.1:8000/chat")
API_KEY = os.getenv("DIAG_API_KEY", "")


def _extract_tokens_from_sse_text(raw_text: str):
    """SSE 원문 텍스트에서 token 이벤트의 content만 순서대로 추출."""
    tokens = []
    for line in raw_text.split("\n"):
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data_str = line[len("data:"):].strip()
        if data_str in ("", "[DONE]"):
            continue
        try:
            event = json.loads(data_str)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "token":
            tokens.append(event["content"])
    return tokens


def method_a_iter_lines(query: str) -> str:
    """A) iter_lines(decode_unicode=True)로 한 줄씩 읽는 방식 (기존 테스트 방식)."""
    resp = requests.post(
        API_URL,
        headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
        json={"query": query},
        stream=True,
    )
    tokens = []
    for line in resp.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data:"):
            continue
        data_str = line[len("data:"):].strip()
        if data_str in ("", "[DONE]"):
            continue
        try:
            event = json.loads(data_str)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "token":
            tokens.append(event["content"])
    return "".join(tokens)


def method_b_full_content(query: str) -> str:
    """B) 응답 전체를 바이트로 다 받은 뒤 한 번에 디코딩하는 방식."""
    resp = requests.post(
        API_URL,
        headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
        json={"query": query},
        stream=True,
    )
    raw_bytes = resp.content  # 전체 바이트를 다 받을 때까지 대기
    raw_text = raw_bytes.decode("utf-8")
    return "".join(_extract_tokens_from_sse_text(raw_text))


def run_once(query: str, run_idx: int) -> None:
    print(f"\n{'=' * 60}\n[실행 {run_idx}] 질의: {query}\n{'=' * 60}")

    text_a = method_a_iter_lines(query)
    corrupted_a = "\ufffd" in text_a
    print(f"[A: iter_lines(decode_unicode=True)] {'🚨 깨짐 발견' if corrupted_a else '✅ 깨끗함'}")

    text_b = method_b_full_content(query)
    corrupted_b = "\ufffd" in text_b
    print(f"[B: resp.content 전체 후 일괄 디코딩] {'🚨 깨짐 발견' if corrupted_b else '✅ 깨끗함'}")

    if corrupted_a and not corrupted_b:
        print("=> A만 깨짐: 클라이언트의 청크 단위 읽기 방식이 원인. 서버는 무죄일 가능성 높음.")
    elif corrupted_a and corrupted_b:
        print("=> 둘 다 깨짐: 서버가 보내는 바이트 자체가 이미 손상됨. 서버 쪽 문제.")
    elif not corrupted_a and not corrupted_b:
        print("=> 둘 다 깨끗함: 이번 실행에서는 재현 안 됨 (간헐적일 수 있음, --repeat로 반복 확인).")
    else:
        print("=> B만 깨짐(드문 경우): 재현 조건을 다시 검토 필요.")


def main() -> None:
    parser = argparse.ArgumentParser(description="2단계: 클라이언트 읽기 방식 비교 격리 테스트")
    parser.add_argument("query", type=str, help="테스트할 질문")
    parser.add_argument("--repeat", type=int, default=1, help="반복 횟수 (기본 1)")
    args = parser.parse_args()

    if not API_KEY:
        print("⚠️ DIAG_API_KEY 환경변수가 비어있습니다. API_KEYS가 설정된 서버라면 인증에 실패합니다.")

    for i in range(args.repeat):
        run_once(args.query, i + 1)


if __name__ == "__main__":
    main()