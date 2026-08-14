"""격리 테스트 1단계: NVIDIA <-> 우리 서버 구간만 순수하게 테스트.

FastAPI, SSE 릴레이, 클라이언트를 전부 배제하고 rag/generator.py의
generate_response_stream()을 직접 호출한다. 여기서 이미 �(U+FFFD)가
보이면, 문제가 "NVIDIA 응답을 우리가 받는 지점"(또는 그보다 상류)에
있다는 뜻이고, 여기서 안 보이면 문제는 "우리 서버 -> 클라이언트" 구간
또는 "클라이언트의 읽기 방식"에 있다는 뜻이다.

사용법:
    python scripts/diag_stream_direct.py "질문 텍스트"
    python scripts/diag_stream_direct.py "질문 텍스트" --repeat 5   # 간헐적 재현 여부 확인용
"""
import argparse
import sys
from pathlib import Path

# app/scripts/ -> scripts/ -> app/ -> 프로젝트 루트(TP01-korea-law-property-crime-rag) 경로 등록
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.config import RAGConfig  # noqa: E402
from rag.bot import LegalRAGBot  # noqa: E402
from rag.generator import generate_response_stream  # noqa: E402


def run_once(bot: LegalRAGBot, query: str, run_idx: int) -> bool:
    """한 번 실행. 깨진 문자가 발견되면 True를 반환."""
    print(f"\n{'=' * 60}\n[실행 {run_idx}] 질의: {query}\n{'=' * 60}")

    docs = bot.retrieve(query)
    if not docs:
        print("⚠️ 검색 결과가 없어 스킵합니다.")
        return False

    collected = []
    found_corruption = False
    for token in generate_response_stream(bot.stream_session, bot.config, bot.logger, query, docs):
        collected.append(token)
        print(token, end="", flush=True)
        if "\ufffd" in token:
            found_corruption = True

    print()  # 줄바꿈
    full_text = "".join(collected)

    if found_corruption or "\ufffd" in full_text:
        print(f"\n🚨 [실행 {run_idx}] 깨진 문자(U+FFFD) 발견! -> NVIDIA<->우리 서버 구간이 원인일 가능성")
        return True
    else:
        print(f"\n✅ [실행 {run_idx}] 깨진 문자 없음")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="1단계: NVIDIA<->우리 서버 구간 격리 테스트")
    parser.add_argument("query", type=str, help="테스트할 질문")
    parser.add_argument("--repeat", type=int, default=1, help="반복 횟수 (간헐적 재현 확인용, 기본 1)")
    args = parser.parse_args()

    config = RAGConfig()
    with LegalRAGBot(config) as bot:
        results = [run_once(bot, args.query, i + 1) for i in range(args.repeat)]

    corrupted_count = sum(results)
    print(f"\n{'#' * 60}")
    print(f"# 최종 결과: {args.repeat}회 중 {corrupted_count}회 깨짐 발견")
    print(f"{'#' * 60}")
    if corrupted_count > 0:
        print("-> NVIDIA<->우리 서버 구간(또는 그 상류)이 원인일 가능성이 높습니다.")
    else:
        print("-> 이 구간은 깨끗합니다. 문제는 '우리 서버->클라이언트' 구간이나")
        print("   '클라이언트의 읽기 방식'에 있을 가능성이 높습니다. 2단계로 넘어가세요.")


if __name__ == "__main__":
    main()