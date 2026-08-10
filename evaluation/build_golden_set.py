"""골든셋 구축 도구 (자동 라벨링 + 사람 감사).

⚠️ 중요한 한계: LLM 자동 판정은 "정답"이 아니라 "그럴듯한 추정"입니다.
법률 문서는 미묘한 요건 차이로 결론이 갈리는 경우가 많아, 자동 라벨링
결과를 검증 없이 그대로 신뢰하면 골든셋 자체가 잘못된 채로 굳어질 수
있습니다. 따라서 아래 3단계 워크플로우를 권장합니다.

    1) --auto   : LLM이 후보 문서마다 "관련 있는지"를 판정해 자동으로 채움
    2) --audit  : 자동 판정 결과만 사람이 훑어보며 확인/수정 (0에서부터
                  판단하는 것보다 훨씬 부담이 적음)
    3) --review-pending : 자동 판정을 아예 신뢰하지 않고 싶은 질의는
                  기존 방식대로 완전 수동으로 처리 가능 (선택 사항)

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

# 판정용 모델은 최종 답변 생성 모델(예: nemotron-3-ultra-550b)보다 훨씬
# 가볍고 빠른 모델을 쓴다. 판정은 후보 문서 개수만큼 반복 호출되므로
# 큰 모델을 쓰면 골든셋 하나 만드는 데 너무 오래 걸린다.
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "meta/llama-3.1-8b-instruct")

JUDGE_SYSTEM_PROMPT = """당신은 한국 법률 정보 검색 시스템(RAG)의 엄격한 평가자입니다.
[사용자 질문]에 대해 [후보 문서]가 실질적으로 관련된 근거자료로 사용될 수 있는지 판단하십시오.

판단 기준 (엄격하게 적용):
- 문서가 질문의 핵심 쟁점에 대해 "구체적인 법리·판단 기준·결론"을 제시해야만 관련 있음
- 같은 죄명(배임, 횡령, 사기 등)이 언급된다는 이유만으로는 절대 관련 있다고 판단하지 말 것
- 질문이 다루는 구체적 사실관계(예: 담보 없는 자금대여, 용도제한 자금 전용 등)와
  실질적으로 겹치는 법리를 다뤄야 함. 단순히 같은 법 조문·용어가 등장하는 것만으로는 부족함
- 판단이 애매하면 relevant=false로 판단할 것 (과대 판정보다 과소 판정이 안전함)

예시:
- 질문: "담보 없이 자금 대여 시 배임죄 성립 여부는?"
  문서A: "이사가 손해 발생을 알면서 담보 없이 만연히 대여한 경우 배임죄 성립"
  → relevant=true (질문의 쟁점에 대한 구체적 판단 기준 제시)
  문서B: "회사 공금을 개인적으로 유용하여 횡령죄로 기소된 사건" (담보·대여 언급 없음)
  → relevant=false (죄명은 다르지만 겹치나, 질문의 핵심 쟁점을 다루지 않음)

반드시 아래 JSON 형식으로만 답하고, 다른 텍스트는 절대 추가하지 마십시오.
{"relevant": true 또는 false, "reason": "한 문장 이유"}
"""


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
# LLM-as-judge 자동 라벨링
# ---------------------------------------------------------------------
def llm_judge_relevance(bot: LegalRAGBot, query: str, doc: Dict[str, Any]) -> Dict[str, Any]:
    """LLM에게 질의-문서 쌍의 관련성을 판정시킨다. 실패 시 relevant=False + error 표시."""
    content_snippet = doc["content"][:1500]
    user_content = f"[사용자 질문]\n{query}\n\n[후보 문서] ({doc.get('doc_type', '')}) {doc['title']}\n{content_snippet}"

    payload = {
        "model": JUDGE_MODEL,
        "messages": [
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.0,
        "max_tokens": 200,
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
        return {"relevant": bool(parsed.get("relevant", False)), "reason": str(parsed.get("reason", "")).strip()}
    except Exception as e:  # noqa: BLE001 - 판정 실패는 폭넓게 잡아서 relevant=False로 처리
        return {"relevant": False, "reason": f"판정 실패: {e}", "error": True}


def auto_review_query(bot: LegalRAGBot, query: str, n_candidates: int) -> Tuple[List[str], List[Dict[str, Any]]]:
    docs = bot.retrieve(query)
    details: List[Dict[str, Any]] = []
    relevant_ids: List[str] = []

    for doc in docs:
        case_id = extract_case_id(doc["title"])
        judged = llm_judge_relevance(bot, query, doc)
        details.append({"case_id": case_id, **judged})
        if judged["relevant"]:
            relevant_ids.append(case_id)
        mark = "✅" if judged["relevant"] else "❌"
        print(f"  {mark} ({case_id}) {judged['reason']}")

    return relevant_ids, details


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
# 사람 감사 (자동 판정 결과만 확인/수정 — 훨씬 가벼운 작업)
# ---------------------------------------------------------------------
def audit_item(item: Dict[str, Any]) -> None:
    print(f"\n[감사] {item['id']}: {item['query']}")
    print(f"  판정 모델: {JUDGE_MODEL}")
    details = item.get("auto_label_details", [])
    if not details:
        print("  (자동 판정 상세 기록 없음)")
    for d in details:
        mark = "✅ 관련" if d.get("relevant") else "❌ 비관련"
        flag = " ⚠️판정실패" if d.get("error") else ""
        print(f"  {mark}{flag} ({d.get('case_id')}) {d.get('reason', '')}")

    print(f"\n  현재 확정된 정답: {item.get('relevant_case_ids') or '(없음)'}")
    raw = input("  이대로 확정하려면 Enter / 수정하려면 새 사건번호 목록(쉼표) 입력 / 건너뛰려면 s: ").strip()

    if raw.lower() == "s":
        return
    if raw:
        item["relevant_case_ids"] = [c.strip() for c in raw.split(",") if c.strip()]
    item["reviewed"] = True
    item["notes"] = f"LLM 자동 라벨링({JUDGE_MODEL}) 후 사람 검증 완료"


def main() -> None:
    parser = argparse.ArgumentParser(description="골든셋 구축 도구 (자동 라벨링 + 사람 감사)")
    parser.add_argument("--auto", action="store_true", help="LLM으로 미검토 질의를 자동 라벨링")
    parser.add_argument("--audit", action="store_true", help="자동 라벨링 결과를 사람이 확인/수정")
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
    review_config = replace(base_config, top_k=args.candidates)

    with LegalRAGBot(review_config) as bot:
        if args.auto:
            print(f"\n=== 자동 라벨링 시작 (판정 모델: {JUDGE_MODEL}) ===")
            for item in items:
                if item.get("reviewed") in (True, "auto"):
                    continue
                print(f"\n[자동] {item['id']}: {item['query'][:50]}...")
                relevant_ids, details = auto_review_query(bot, item["query"], args.candidates)
                item["relevant_case_ids"] = relevant_ids
                item["reviewed"] = "auto"
                item["auto_label_details"] = details
                item["notes"] = f"LLM 자동 라벨링({JUDGE_MODEL}) - 사람 검증 필요 (--audit)"
                save_all(GOLDEN_SET_PATH, items)
            print("\n✅ 자동 라벨링 완료. `--audit`로 결과를 확인/수정하세요.")

        if args.audit:
            targets = [i for i in items if i.get("reviewed") == "auto"]
            if not targets:
                print("\n감사할 자동 라벨링 항목이 없습니다. 먼저 --auto를 실행하세요.")
            for item in targets:
                audit_item(item)
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
