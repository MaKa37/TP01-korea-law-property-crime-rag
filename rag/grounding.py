"""생성된 답변의 근거 검증 (grounding check).

LLM이 검색된 참고 자료에 없는 판례 번호를 지어내는 사례가 실제로
관찰됐다 (검색 결과 5건 중 어디에도 없는 "2004도5167"을 인용, 심지어
같은 사건번호에 선고일을 2004년/2006년으로 서로 다르게 적기까지 함).

재시도/타임아웃/폴백 같은 장치는 전부 "API 호출 자체가 실패하는" 상황만
막아준다. "API는 성공했지만 내용이 틀린" 경우는 이런 장치로 못 잡는다.
이 모듈은 생성된 텍스트에서 판례 번호 패턴을 추출해서, 실제 검색된
문서에 등장하는 번호인지 대조한다.

⚠️ 완벽한 검증이 아니다. 정규식 기반 패턴 매칭이라 오탐/누락이 있을 수
있고, "번호는 맞는데 인용된 법리가 실제로 그 판례 내용과 다르다"처럼
더 미묘한 hallucination은 못 잡는다. 그래도 "존재하지 않는 판례 번호를
지어내는" 명백한 사례는 확실히 잡아낸다.
"""
import re
from typing import Any, Dict, List

# 한국 판례/사건번호 패턴: 연도(2~4자리) + 사건종류(한글 1~4자) + 일련번호
# 예: "2004도5167", "2011고합187", "84누692", "91두1", "74그4".
# 법조문 인용("형법 제355조")은 한글이 숫자 "앞"에 오므로 이 패턴에
# 걸리지 않는다 (숫자-한글-숫자 순서만 매칭).
CITATION_PATTERN = re.compile(r"\d{2,4}[가-힣]{1,4}\d+")


def extract_citations(text: str) -> List[str]:
    if not text:
        return []
    return CITATION_PATTERN.findall(text)


def check_grounding(answer: str, retrieved_docs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """답변에 등장한 판례 번호가 실제 검색된 문서(제목+본문)에 존재하는지 검증.

    반환값:
        total_citations: 답변에서 발견된 판례 번호 인용 개수
        unverified_citations: 검색 문서 어디에도 없는 번호 목록
        has_unverified: 하나라도 있으면 True
    """
    known_citations = set()
    for doc in retrieved_docs:
        known_citations.update(extract_citations(doc.get("title", "")))
        known_citations.update(extract_citations(doc.get("content", "")))

    answer_citations = extract_citations(answer)
    unverified = sorted({c for c in answer_citations if c not in known_citations})

    return {
        "total_citations": len(answer_citations),
        "unverified_citations": unverified,
        "has_unverified": len(unverified) > 0,
    }