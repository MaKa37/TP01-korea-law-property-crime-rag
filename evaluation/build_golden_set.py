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
        "max_tokens": 250,
        "stream": False,
    }

    try:
        resp = bot.session.post(
            bot.config.chat_url,
            json=payload,
            timeout=(bot.config.connect_timeout, bot.config.chat_timeout),
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"].strip()
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
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

    for doc in docs:
        case_id = extract_case_id(doc["title"])
        judged = llm_judge_relevance(bot, judge_model, query, doc)
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
    args = parser.parse_args()

    items = load_all(GOLDEN_SET_PATH)

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