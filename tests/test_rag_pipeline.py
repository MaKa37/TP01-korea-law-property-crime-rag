"""
tests/test_rag_pipeline.py
==========================
RAG 파이프라인 핵심 구성 요소(가드레일, 근거검증) 단위/통합 테스트
"""

import sys
from pathlib import Path

# [경로 자동 등록]
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"

for path in [str(PROJECT_ROOT), str(SRC_DIR)]:
    if path not in sys.path:
        sys.path.insert(0, path)

import pytest
import orchestration.guardrails as gr_module
from rag.grounding import LegalGroundingVerifier


# =============================================================================
# [Helper] guardrails.py 내 위기 감지 함수/메서드 자동 탐색
# =============================================================================
def _find_crisis_detector():
    """guardrails.py에 정의된 다양한 이름의 위기 감지 함수/클래스를 탐색하여 호출 가능한 형태로 반환"""
    candidate_names = [
        "is_crisis_query",
        "check_crisis_signals",
        "check_crisis",
        "detect_crisis",
        "is_crisis",
        "filter_crisis",
    ]
    for name in candidate_names:
        if hasattr(gr_module, name):
            fn = getattr(gr_module, name)
            if callable(fn):
                return lambda q: bool(fn(q))

    # 클래스 형태로 구현된 경우 (예: Guardrail, CrisisDetector 등)
    for attr_name in dir(gr_module):
        attr = getattr(gr_module, attr_name)
        if isinstance(attr, type):
            instance = attr()
            for method_name in ["check", "detect", "is_crisis", "verify"]:
                if hasattr(instance, method_name):
                    method = getattr(instance, method_name)
                    return lambda q: bool(method(q))

    # 탐색 실패 시 guardrails.py에 선언된 목록 출력
    available = [a for a in dir(gr_module) if not a.startswith("_")]
    raise AttributeError(
        f"guardrails.py에서 위기 감지 함수를 찾을 수 없습니다. (현재 정의된 목록: {available})"
    )


# =============================================================================
# [1] 위기 신호 가드레일 테스트
# =============================================================================
def test_crisis_guardrail():
    crisis_detector = _find_crisis_detector()

    crisis_queries = [
        "사기당해서 너무 살기 싫고 죽고 싶어요",
        "극단적 선택을 생각하고 있습니다 도와주세요",
    ]
    for q in crisis_queries:
        assert crisis_detector(q) is True, f"위기 신호 미감지: {q}"

    normal_queries = [
        "중고거래 사기 고소장 작성하는 방법 알려주세요",
        "빌려준 돈 500만원을 안 갚는데 사기죄가 되나요?",
    ]
    for q in normal_queries:
        assert crisis_detector(q) is False, f"정상 쿼리 오탐: {q}"


# =============================================================================
# [2] 근거 검증(Grounding) 정상 케이스 테스트
# =============================================================================
def test_grounding_verifier_success():
    verifier = LegalGroundingVerifier()

    retrieved_docs = [
        {
            "title": "판례 [99도4923] 특정경제범죄가중처벌등에관한법률위반 (요약)",
            "content": "형법 제355조 및 형법 제356조의 업무상배임죄에 관한 판례이다.",
        }
    ]

    valid_answer = "대법원 99도4923 판결에 따르면, 형법 제356조 업무상 배임죄가 적용될 수 있습니다."
    is_grounded, details = verifier.verify(valid_answer, retrieved_docs)

    assert is_grounded is True
    assert len(details["ungrounded_cases"]) == 0
    assert len(details["ungrounded_statutes"]) == 0


# =============================================================================
# [3] 근거 검증(Grounding) 허위 인용(Hallucination) 차단 테스트
# =============================================================================
def test_grounding_verifier_hallucination_detection():
    verifier = LegalGroundingVerifier()

    retrieved_docs = [
        {
            "title": "판례 [99도4923] 업무상배임 (요약)",
            "content": "형법 제356조 업무상배임죄 요건을 다룬다.",
        }
    ]

    # 미포함 판례(2021도1234) 및 미포함 조문(형법 제347조) 생성
    hallucinated_answer = "대법원 2021도1234 판결과 형법 제347조에 의해 사기죄가 성립합니다."
    is_grounded, details = verifier.verify(hallucinated_answer, retrieved_docs)

    assert is_grounded is False
    assert "2021도1234" in details["ungrounded_cases"]
    assert "형법제347조" in details["ungrounded_statutes"]