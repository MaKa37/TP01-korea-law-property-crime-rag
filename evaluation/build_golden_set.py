"""골든셋 구축 도구 (자동 라벨링 + 확신도 기반 선택적 사람 감사).

⚠️ 중요한 한계: LLM 자동 판정은 "정답"이 아니라 "그럴듯한 추정"입니다.
아무리 크고 좋은 모델을 써도 법률 문서의 미묘한 요건 차이를 완벽히
판단하지 못할 수 있습니다. 이 골든셋은 나중에 시스템 품질을 재는 자(尺)
역할을 하므로, 자(尺) 자체가 틀리면 이후 모든 튜닝이 잘못된 방향으로
갈 수 있습니다.

그래서 완전 자동도, 완전 수동도 아닌 절충안을 씁니다:
    1) --auto   : LLM이 후보 문서마다 "관련 있는지 + 확신도"를 판정.
                  확신도가 high인 판정은 그대로 자동 확정(reviewed=True).
                  확신도가 낮거나 애매한 것만 사람 감사 대상(reviewed="auto")으로 남김.
    2) --audit  : 확신도 낮은 항목만 사람이 훑어보며 확인/수정
                  (전체를 검토하는 것보다 부담이 훨씬 적음)
    3) --review-pending : 자동 판정을 아예 신뢰하지 않고 싶은 질의는
                  기존 방식대로 완전 수동으로 처리 가능 (선택 사항)

판정 모델은 기본적으로 core/config.py의 chat_model(최종 답변용 대형 모델)을
그대로 씁니다. 골든셋 구축은 실시간 응답이 아니라 한 번 하는 배치
작업이므로, 속도보다 판단 품질을 우선한다. 다른 모델을 쓰고 싶으면
.env에 JUDGE_MODEL을 지정하세요.

사용 예:
    python evaluation/build_golden_set.py --auto
    python evaluation/build_golden_set.py --audit
    python evaluation/build_golden_set.py --add "새 질문"
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import RAGConfig  # noqa: E402
from rag.bot import LegalRAGBot  # noqa: E402
from evaluation.run_eval import extract_case_id  # noqa: E402

GOLDEN_SET_PATH = Path(__file__).resolve().parent / "golden_set.jsonl"

# JUDGE_MODEL을 명시적으로 지정하지 않으면, config.chat_model(최종 답변용
# 대형 모델)을 그대로 판정에도 쓴다. main()에서 실제 값으로 해석된다.
JUDGE_MODEL_OVERRIDE = os.getenv("JUDGE_MODEL")

# 판정 전용 설정. 배치 작업(실시간 응답 아님)이므로 속도보다 안정성을 우선한다.
#   - JUDGE_CALL_DELAY_SEC: 판정 호출 사이 대기 시간. 550B급 모델을 후보
#     개수만큼(예: 12번) 연달아 두들기면 NVIDIA API가 503(과부하)을 자주
#     반환한다. 텀을 줘서 요청 빈도를 낮춘다.
#   - JUDGE_TIMEOUT_SEC: 판정 1회당 read timeout. 대화형 채팅(chat_timeout,
#     기본 60초)과 달리 배치 작업이라 훨씬 오래 기다려도 무방하다. 550B
#     모델의 비스트리밍 호출은 전체 응답이 완성될 때까지 한 번에 기다려야
#     하므로 여유가 필요하다.
JUDGE_CALL_DELAY_SEC = float(os.getenv("JUDGE_CALL_DELAY_SEC", "1.5"))
JUDGE_TIMEOUT_SEC = int(os.getenv("JUDGE_TIMEOUT_SEC", "180"))
# ⚠️ nemotron-3-ultra-550b-a55b 같은 추론(reasoning) 모델은 최종 답변을
# 내놓기 전에 내부적으로 "생각하는" 토큰을 먼저 소비한다. max_tokens가
# 작으면(예: 250) 생각만 하다가 예산을 다 써버려 실제 답변(JSON)은 한
# 글자도 못 내놓고 끝날 수 있다. 짧은 JSON 하나 받자고 이렇게 크게
# 잡는 게 낭비처럼 보이지만, 추론 모델에게는 "생각할 공간"이 필요하다.
JUDGE_MAX_TOKENS = int(os.getenv("JUDGE_MAX_TOKENS", "1500"))
# 후보 문서 판정을 동시에 몇 개까지 병렬로 쏠지. 순차 처리(1개씩)가 너무
# 느려서 도입했지만, 너무 크게 잡으면 과부하(503)가 재발할 수 있으니
# 보수적으로 시작해서 필요하면 올릴 것.
JUDGE_CONCURRENCY = int(os.getenv("JUDGE_CONCURRENCY", "4"))

JUDGE_SYSTEM_PROMPT = """당신은 한국 법률 정보 검색 시스템(RAG)의 엄격한 평가자입니다.
[사용자 질문]에 대해 [후보 문서]가 실질적으로 관련된 근거자료로 사용될 수 있는지 판단하십시오.

판단 기준 (엄격하게 적용):
- 문서가 질문의 핵심 쟁점에 대해 "구체적인 법리·판단 기준·결론·증거"를 제시해야만 관련 있음
- 같은 죄명(배임, 횡령, 사기 등)이 언급된다는 이유만으로는 절대 관련 있다고 판단하지 말 것
- 질문이 다루는 구체적 사실관계(예: 담보 없는 자금대여, 용도제한 자금 전용, 특정 증거 요건 등)와
  실질적으로 겹치는 내용이어야 함. 단순히 같은 법 조문·용어가 등장하는 것만으로는 부족함

confidence(확신도) 기준 — 반드시 솔직하게 판단할 것:
- high: 문서가 질문의 핵심 쟁점을 직접적으로 다루거나(관련 있음), 명백히 무관함(관련 없음)이 의심의 여지 없이 분명함
- medium: 관련성이 있어 보이지만 완전히 확신하기는 어려움 (예: 비슷한 사안이지만 세부 요건이 다를 수 있음)
- low: 판단하기 애매함, 사람의 확인이 필요함

애매하면 절대 억지로 high를 고르지 말고 medium/low로 솔직하게 표시하십시오.
확신도를 부풀리는 것보다, 애매함을 인정하고 사람 검토로 넘기는 것이 훨씬 안전합니다.

반드시 아래 JSON 형식으로만 답하고, 다른 텍스트는 절대 추가하지 마십시오.
{"relevant": true 또는 false, "confidence": "high" 또는 "medium" 또는 "low", "reason": "한 문장 이유"}
"""

_VALID_CONFIDENCE = ("high", "medium", "low")


def _all_judgments_errored(item: Dict[str, Any]) -> bool:
    """자동 판정이 (거의) 전부 실패했는지 확인.

    503/빈 응답 등으로 후보 문서 전부에 대한 판정이 실패한 채로 reviewed="auto"가
    저장된 경우, --audit으로 봐도 "판정 실패"만 잔뜩 보이고 실제로 검토할
    내용이 없다. 이런 항목은 감사 대상이 아니라 재시도 대상이다.
    """
    details = item.get("auto_label_details", [])
    if not details:
        return False
    return all(d.get("error") for d in details)


def load_all(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def save_all(path: Path, items: List[Dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------
# LLM-as-judge 자동 라벨링 (확신도 포함)
# ---------------------------------------------------------------------
def llm_judge_relevance(bot: LegalRAGBot, judge_model: str, query: str, doc: Dict[str, Any]) -> Dict[str, Any]:
    """LLM에게 질의-문서 쌍의 관련성 + 확신도를 판정시킨다. 실패 시 confidence=low로 처리."""
    content_snippet = doc["content"][:1500]
    user_content = f"[사용자 질문]\n{query}\n\n[후보 문서] ({doc.get('doc_type', '')}) {doc['title']}\n{content_snippet}"

    payload = {
        "model": judge_model,
        "messages": [
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.0,
        "max_tokens": JUDGE_MAX_TOKENS,
        "stream": False,
    }

    try:
        resp = bot.session.post(
            bot.config.chat_url,
            json=payload,
            timeout=(bot.config.connect_timeout, JUDGE_TIMEOUT_SEC),
        )
        resp.raise_for_status()
        message = resp.json()["choices"][0]["message"]
        text = (message.get("content") or "").strip()
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()

        if not text:
            choice = resp.json()["choices"][0]
            finish_reason = choice.get("finish_reason")
            # nemotron-3-ultra 같은 추론(reasoning) 모델은 최종 답변 전에
            # "생각하는" 토큰을 먼저 쓰고, 그 내용이 content가 아니라
            # reasoning_content(또는 reasoning) 필드에 별도로 담기기도 한다.
            # finish_reason="length"이면서 이 필드에 내용이 있다면,
            # max_tokens가 부족해서 생각만 하다 끝난 것이다.
            reasoning_len = len(message.get("reasoning_content") or message.get("reasoning") or "")
            reason_detail = f"finish_reason={finish_reason}, reasoning_len={reasoning_len}"
            return {
                "relevant": False, "confidence": "low",
                "reason": f"빈 응답 ({reason_detail})", "error": True
            }

        parsed = json.loads(text)

        confidence = parsed.get("confidence")
        if confidence not in _VALID_CONFIDENCE:
            confidence = "low"  # 형식이 이상하면 안전하게 low로 취급 (사람 검토 유도)

        return {
            "relevant": bool(parsed.get("relevant", False)),
            "confidence": confidence,
            "reason": str(parsed.get("reason", "")).strip(),
        }
    except Exception as e:  # noqa: BLE001 - 판정 실패는 폭넓게 잡아서 사람 검토로 유도
        return {"relevant": False, "confidence": "low", "reason": f"판정 실패: {e}", "error": True}


def auto_review_query(
    bot: LegalRAGBot,
    judge_model: str,
    query: str,
    n_candidates: int
) -> Tuple[List[str], List[Dict[str, Any]], bool]:
    """자동 라벨링 실행. (정답 사건번호 목록, 상세 기록, 사람 검토 필요 여부)를 반환한다.

    사람 검토가 필요한 경우:
      - 관련 있다고 판정된 문서 중 confidence가 high가 아닌 게 하나라도 있음
      - 판정 자체가 실패한 문서가 있음 (error=True)
      - 관련 문서를 하나도 못 찾음 (커버리지 문제일 수 있어 확인 필요)
    """
    docs = bot.retrieve(query)
    details: List[Dict[str, Any]] = []
    relevant_ids: List[str] = []
    needs_review = False

    def _judge_one(doc: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        case_id = extract_case_id(doc["title"])
        judged = llm_judge_relevance(bot, judge_model, query, doc)
        if JUDGE_CALL_DELAY_SEC > 0:
            time.sleep(JUDGE_CALL_DELAY_SEC)  # 워커 하나당 완급 조절 (완전 무제한 동시 요청 방지)
        return case_id, judged

    # executor.map은 입력 순서를 그대로 유지해서 결과를 돌려주므로,
    # 동시에 실행되면서도 출력은 리랭크 점수 순서 그대로 유지된다.
    with ThreadPoolExecutor(max_workers=JUDGE_CONCURRENCY) as executor:
        for case_id, judged in executor.map(_judge_one, docs):
            details.append({"case_id": case_id, **judged})

            if judged.get("error"):
                needs_review = True

            if judged["relevant"]:
                relevant_ids.append(case_id)
                if judged.get("confidence") != "high":
                    needs_review = True

            mark = "✅" if judged["relevant"] else "❌"
            conf = judged.get("confidence", "?")
            print(f"  {mark} [{conf:6s}] ({case_id}) {judged['reason']}")

    if not relevant_ids:
        needs_review = True  # 0건도 사람이 한 번은 확인하는 게 안전

    return relevant_ids, details, needs_review


# ---------------------------------------------------------------------
# 완전 수동 검토 (자동 판정을 아예 안 믿고 싶은 경우)
# ---------------------------------------------------------------------
def manual_review_query(bot: LegalRAGBot, query: str, n_candidates: int) -> Optional[List[str]]:
    print(f"\n질의: {query}")
    print("(관련 있는 판례 번호를 쉼표로 입력. 예: 1,3 / 없으면 Enter / 건너뛰려면 's')\n")

    docs = bot.retrieve(query)
    if not docs:
        print("  ⚠️ 검색 결과가 없습니다.")
        return []

    for i, doc in enumerate(docs, 1):
        case_id = extract_case_id(doc["title"])
        snippet = doc["content"][:120].replace("\n", " ")
        print(f"  [{i}] ({case_id}) {doc['title'][:60]}")
        print(f"      {snippet}...")

    choice = input("\n  선택 > ").strip()
    if choice.lower() == "s":
        return None
    if not choice:
        return []

    selected = []
    for token in choice.split(","):
        token = token.strip()
        if token.isdigit():
            idx = int(token) - 1
            if 0 <= idx < len(docs):
                selected.append(extract_case_id(docs[idx]["title"]))
    return selected


# ---------------------------------------------------------------------
# 사람 감사 (확신도 낮은 자동 판정만 확인/수정 — 훨씬 가벼운 작업)
# ---------------------------------------------------------------------
def audit_item(item: Dict[str, Any], judge_model: str) -> None:
    print(f"\n[감사] {item['id']}: {item['query']}")
    print(f"  판정 모델: {judge_model}")
    details = item.get("auto_label_details", [])
    if not details:
        print("  (자동 판정 상세 기록 없음)")
    for d in details:
        mark = "✅ 관련" if d.get("relevant") else "❌ 비관련"
        conf = d.get("confidence", "?")
        flag = " ⚠️판정실패" if d.get("error") else ""
        # 확신도가 낮은 항목을 눈에 띄게 표시 (여기가 실제로 봐야 할 부분)
        highlight = " 👈 확신도 낮음" if d.get("relevant") and conf != "high" else ""
        print(f"  {mark} [{conf:6s}]{flag} ({d.get('case_id')}) {d.get('reason', '')}{highlight}")

    print(f"\n  현재 확정된 정답: {item.get('relevant_case_ids') or '(없음)'}")
    raw = input("  이대로 확정하려면 Enter / 수정하려면 새 사건번호 목록(쉼표) 입력 / 건너뛰려면 s: ").strip()

    if raw.lower() == "s":
        return
    if raw:
        item["relevant_case_ids"] = [c.strip() for c in raw.split(",") if c.strip()]
    item["reviewed"] = True
    item["notes"] = f"LLM 자동 라벨링({judge_model}) 후 사람 검증 완료"


def main() -> None:
    parser = argparse.ArgumentParser(description="골든셋 구축 도구 (자동 라벨링 + 확신도 기반 선택적 감사)")
    parser.add_argument("--auto", action="store_true", help="LLM으로 미검토 질의를 자동 라벨링")
    parser.add_argument("--audit", action="store_true", help="확신도 낮은 자동 판정만 사람이 확인/수정")
    parser.add_argument("--review-pending", action="store_true", help="완전 수동으로 검토 (자동 판정 없이)")
    parser.add_argument("--add", type=str, default=None, help="새 질의를 추가")
    parser.add_argument("--candidates", type=int, default=12, help="검토용 후보 개수 (기본 12)")
    parser.add_argument(
        "--reset-failed", action="store_true",
        help="자동 판정이 전부 실패(error)한 질의를 재시도 대기 상태로 되돌림"
    )
    args = parser.parse_args()

    items = load_all(GOLDEN_SET_PATH)

    if args.reset_failed:
        reset_count = 0
        for item in items:
            if item.get("reviewed") == "auto" and _all_judgments_errored(item):
                item["reviewed"] = False
                item["relevant_case_ids"] = []
                item.pop("auto_label_details", None)
                item["notes"] = "판정 전부 실패 -> --reset-failed로 재시도 대기 상태로 되돌림"
                reset_count += 1
        save_all(GOLDEN_SET_PATH, items)
        print(f"🔄 {reset_count}건을 재시도 대기 상태로 되돌렸습니다. 다시 --auto를 실행하세요.")
        if not any([args.auto, args.audit, args.review_pending, args.add]):
            return

    if args.add:
        next_num = len(items) + 1
        items.append({
            "id": f"q{next_num:03d}",
            "query": args.add,
            "relevant_case_ids": [],
            "reviewed": False,
            "notes": "build_golden_set.py로 추가됨",
        })
        save_all(GOLDEN_SET_PATH, items)
        print(f"➕ q{next_num:03d} 추가됨.")

    if not any([args.auto, args.audit, args.review_pending, args.add]):
        parser.print_help()
        return
    if not any([args.auto, args.audit, args.review_pending]):
        return  # --add만 실행한 경우

    base_config = RAGConfig()
    judge_model = JUDGE_MODEL_OVERRIDE or base_config.chat_model
    review_config = replace(base_config, top_k=args.candidates)

    with LegalRAGBot(review_config) as bot:
        if args.auto:
            print(f"\n=== 자동 라벨링 시작 (판정 모델: {judge_model}) ===")
            auto_confirmed, needs_audit = 0, 0
            for item in items:
                if item.get("reviewed") in (True, "auto"):
                    continue
                print(f"\n[자동] {item['id']}: {item['query'][:50]}...")
                relevant_ids, details, needs_review = auto_review_query(bot, judge_model, item["query"], args.candidates)
                item["relevant_case_ids"] = relevant_ids
                item["auto_label_details"] = details

                if needs_review:
                    item["reviewed"] = "auto"
                    item["notes"] = f"LLM 자동 라벨링({judge_model}) - 확신도 낮음, 검증 필요 (--audit)"
                    needs_audit += 1
                    print("  → 확신도 낮음, --audit 대상으로 남김")
                else:
                    item["reviewed"] = True
                    item["notes"] = f"LLM 자동 라벨링({judge_model}) - 고신뢰도, 자동 확정"
                    auto_confirmed += 1
                    print("  → 고신뢰도, 자동 확정")

                save_all(GOLDEN_SET_PATH, items)
            print(f"\n✅ 자동 라벨링 완료. 자동 확정 {auto_confirmed}건 / 사람 검토 필요 {needs_audit}건")
            if needs_audit:
                print("   `--audit`로 검토 필요 항목만 확인하세요.")

        if args.audit:
            targets = [i for i in items if i.get("reviewed") == "auto"]
            if not targets:
                print("\n감사할 항목이 없습니다 (모두 고신뢰도로 자동 확정됐거나, --auto를 먼저 실행해야 함).")
            for item in targets:
                audit_item(item, judge_model)
                save_all(GOLDEN_SET_PATH, items)
            print("\n✅ 감사를 마쳤습니다.")

        if args.review_pending:
            for item in items:
                if item.get("reviewed") in (True, "auto"):
                    continue
                result = manual_review_query(bot, item["query"], args.candidates)
                if result is None:
                    print("  ⏭️  건너뜀")
                    continue
                item["relevant_case_ids"] = result
                item["reviewed"] = True
                item["notes"] = "완전 수동 검토 완료"
                save_all(GOLDEN_SET_PATH, items)

    remaining = [i for i in items if i.get("reviewed") not in (True, "auto")]
    if remaining:
        print(f"\n⏭️  아직 미처리: {len(remaining)}건")


if __name__ == "__main__":
    main()