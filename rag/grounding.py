"""
rag/grounding.py
================
LLM이 생성한 답변의 법률적 근거(판례 번호 및 법조문 번호)를
검색된 참고 문서와 대조하여 환각(Hallucination)을 탐지하고 차단하는 모듈입니다.
"""

import re
from typing import Any, Dict, List, Set, Tuple


class LegalGroundingVerifier:
    def __init__(self):
        # 1. 사건번호 패턴: 예) 2019도7370, 99도4923, 2018고합19, 2019가합40778, 97므1486 등
        self.case_no_pattern = re.compile(
            r"\b(\d{2,4}\s*(?:도|고합|고단|노|다|두|므|스|느|나|가합|가단|드단|드합|르)\s*\d+(?:,\s*\d+)*)\b"
        )
        # 2. 법조문 패턴 (앞선 단어와 분리하여 법령명만 정확히 캡처)
        # 예) 형법 제347조, 특정경제범죄가중처벌등에관한법률 제3조제1항, 「민법」 제1115조 등
        self.statute_art_pattern = re.compile(
            r"(?:^|[^\w가-힣])[「『\"']?([가-힣]{1,20}(?:법|법률|규칙|규정|조례))[」』\"']?\s*제\s*(\d+)\s*조(?:의\s*(\d+))?"
        )

    def _normalize_text(self, text: str) -> str:
        """비교를 위한 공백 및 특수기호 정규화"""
        return re.sub(r"\s+", "", text)

    def extract_case_numbers(self, text: str) -> Set[str]:
        """텍스트에서 사건번호 목록 추출 (공백 제거 정규화)"""
        matches = self.case_no_pattern.findall(text)
        return {self._normalize_text(m) for m in matches}

    def extract_statute_articles(self, text: str) -> Set[str]:
        """텍스트에서 법령명+조문번호 조합 추출 (예: '형법제347조')"""
        statutes = set()
        for m in self.statute_art_pattern.finditer(text):
            law_name = self._normalize_text(m.group(1))
            main_art = m.group(2)
            sub_art = f"의{m.group(3)}" if m.group(3) else ""
            statutes.add(f"{law_name}제{main_art}조{sub_art}")
        return statutes

    def verify(
        self,
        generated_answer: str,
        retrieved_documents: List[Dict[str, Any]],
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        생성된 답변과 검색된 문서를 대조 검증합니다.
        
        Returns:
            (is_grounded, details_dict)
        """
        context_text = " ".join(
            f"{doc.get('title', '')} {doc.get('content', '')}"
            for doc in retrieved_documents
        )
        normalized_context = self._normalize_text(context_text)

        # 1. 판례 사건번호 검증
        gen_case_numbers = self.extract_case_numbers(generated_answer)
        ungrounded_cases = [c for c in gen_case_numbers if c not in normalized_context]

        # 2. 법령 조문 검증
        gen_statutes = self.extract_statute_articles(generated_answer)
        ungrounded_statutes = [s for s in gen_statutes if s not in normalized_context]

        has_hallucination = bool(ungrounded_cases or ungrounded_statutes)
        is_grounded = not has_hallucination

        verification_details = {
            "is_grounded": is_grounded,
            "cited_cases": list(gen_case_numbers),
            "ungrounded_cases": ungrounded_cases,
            "cited_statutes": list(gen_statutes),
            "ungrounded_statutes": ungrounded_statutes,
        }

        return is_grounded, verification_details


# 싱글톤 인스턴스
grounding_verifier = LegalGroundingVerifier()


def check_grounding(
    answer: str,
    retrieved_documents: List[Dict[str, Any]],
) -> Tuple[bool, Dict[str, Any]]:
    """bot.py 하위 호환용 함수"""
    return grounding_verifier.verify(answer, retrieved_documents)