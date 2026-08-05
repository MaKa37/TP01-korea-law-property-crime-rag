"""
Legal Data Chunking & Pipeline Orchestrator (v4.3.0 Enterprise Edition)

이 모듈은 한국 법률 데이터(법령, 판례, 법령해석례, 법령용어)를 입력받아
RAG(Retrieval-Augmented Generation) 시스템 및 Vector DB에 최적화된 청크(Chunk)로
비동기/스트리밍 변환 및 정제하는 엔터프라이즈 파이프라인입니다.

Major Features:
    - 대용량 JSON 스트리밍 라이팅 (OOM 방지)
    - 문장 경계 보존 슬라이딩 윈도우 청킹 (Regex 기반)
    - Head/Body 분할 및 대형 판례 Head 청크 오버플로우 방어
    - 법령용어 컬럼형/다중 리스트 자동 언롤링(Unrolling) 파싱
    - 레코드 단위 예외 격리 및 DLQ(Dead Letter Queue) 기록

Author: RAG Pipeline Engineering Team
Date: 2026-08-04
"""

from dataclasses import asdict, dataclass, field
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple, Union

# ==============================================================================
# 버전 관리 변수 (Version Configuration)
# ==============================================================================
VERSION = "4.3.1"


# ==============================================================================
# 0. 로깅 설정 (Logging Configuration)
# ==============================================================================
logger = logging.getLogger("LegalChunker")
logger.setLevel(logging.INFO)

# 기존 핸들러 중복 누적 완벽 방지 (FastAPI/Jupyter/재import 환경 호환)
if logger.hasHandlers():
    logger.handlers.clear()

_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(
    logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
)
logger.addHandler(_handler)

# root logger로의 전파 차단
logger.propagate = False


# ==============================================================================
# 1. 정규식 사전 컴파일 (Pre-compiled Regex Patterns)
# ==============================================================================
HTML_BR_PATTERN = re.compile(r'<br\s*/?>', re.IGNORECASE)
HTML_TAG_PATTERN = re.compile(r'<[^>]+>')
MULTI_SPACE_PATTERN = re.compile(r'[ \t]+')
REF_SPLIT_PATTERN = re.compile(r'\[\d+\]|/|,')
PURE_KOREAN_PATTERN = re.compile(r'[가-힣]')

# 문장 분할 정규식: 숫자 뒤 마침표 제외, 마침표 후 공백 또는 문장 끝 감지
LEGAL_SENTENCE_PATTERN = re.compile(r'(?<!\d)\.(?:\s+|$)')

# 법령 정제 정규식
DELETED_ARTICLE_PATTERN = re.compile(r'^제\s*\d+\s*조(?:의\s*\d+)?\s*(?:\([^)]*\))?\s*삭제\b')
ITEM_SYMBOL_PATTERN = re.compile(r'^[①-⑮\d\.\s\(\)가-하]+')

# 중복 탐지 상수
DUP_PROBE_LEN: int = 25
DUP_PROBE_MIN_LEN: int = 6


# ==============================================================================
# 2. 데이터 구조체 (Data Models)
# ==============================================================================
@dataclass
class LegalChunk:
    """RAG Vector DB 적재용 표준 데이터 모델."""
    chunk_id: str
    doc_type: str
    doc_id: str
    title: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """JSON 직렬화를 위한 Dictionary 변환 함수."""
        return asdict(self)


@dataclass
class DLQItem:
    """파싱 실패 및 데이터 불일치 레코드 격리용 Dead Letter Queue 모델."""
    source_file: str
    raw_id: str
    error_reason: str
    raw_payload: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        """JSON 직렬화를 위한 Dictionary 변환 함수."""
        return asdict(self)


@dataclass
class PipelineMetrics:
    """파이프라인 수행 결과 및 성능 지표 수집 클래스."""
    total_chunks: int = 0
    chunks_by_type: Dict[str, int] = field(default_factory=dict)
    dlq_count: int = 0
    start_time: float = field(default_factory=time.time)

    def record_chunk(self, doc_type: str) -> None:
        """성공 처리된 청크 카운트 증가."""
        self.total_chunks += 1
        self.chunks_by_type[doc_type] = self.chunks_by_type.get(doc_type, 0) + 1

    def record_dlq(self) -> None:
        """DLQ 격리 건수 카운트 증가."""
        self.dlq_count += 1

    def get_summary(self) -> str:
            """파이프라인 통계 리포트 생성."""
            elapsed = time.time() - self.start_time
            divider = "=" * 60
            return (
                f"\n{divider}\n"
                f"📊 Pipeline Execution Performance Report (v{VERSION})\n"
                f"  - Total Elapsed Time : {elapsed:.2f} seconds\n"
                f"  - Total Generated Chunks : {self.total_chunks:,} items\n"
                f"  - Breakdown by Doc Type  : {self.chunks_by_type}\n"
                f"  - DLQ Isolated Count     : {self.dlq_count:,} items\n"
                f"{divider}\n"
            )


# ==============================================================================
# 3. 텍스트 정제 및 청킹 엔진 (Text Processing Engine)
# ==============================================================================
class TextCleaner:
    """법률 문맥 보존형 텍스트 정제 및 문장 분할 헬퍼 클래스."""

    @staticmethod
    def strip_html(text: Any) -> str:
        """HTML 태그 제거 및 줄바꿈 정규화 (다양한 타입 방어적 처리)."""
        if text is None:
            return ""
        if isinstance(text, list):
            text = " ".join([str(item) for item in text if item is not None])
        elif not isinstance(text, str):
            text = str(text)

        cleaned = HTML_BR_PATTERN.sub('\n', text)
        cleaned = HTML_TAG_PATTERN.sub(' ', cleaned)
        return TextCleaner.normalize_whitespace(cleaned)

    @staticmethod
    def normalize_whitespace(text: Any) -> str:
        """연속된 공백 및 불필요한 줄바꿈을 제거하고 인덴트 정리."""
        if text is None:
            return ""
        if not isinstance(text, str):
            text = str(text)

        lines = [MULTI_SPACE_PATTERN.sub(' ', line).strip() for line in text.splitlines()]
        return "\n".join([line for line in lines if line])

    @staticmethod
    def parse_ref_articles(raw_ref: Any) -> List[str]:
        """참조조문 텍스트를 파싱하여 조문 단위를 리스트로 추출."""
        cleaned = TextCleaner.strip_html(raw_ref)
        if not cleaned:
            return []
        items = REF_SPLIT_PATTERN.split(cleaned)
        return [item.strip() for item in items if item.strip()]

    @staticmethod
    def split_by_sentence_boundary(
        text: str, 
        max_chars: int = 1200, 
        overlap: int = 200
    ) -> List[str]:
        """법률 문장 경계(마침표)를 준수하며 슬라이딩 윈도우 방식으로 분할."""
        if len(text) <= max_chars:
            return [text]

        sentences: List[str] = []
        last_idx = 0
        
        for match in LEGAL_SENTENCE_PATTERN.finditer(text):
            end_idx = match.end()
            sent = text[last_idx:end_idx].strip()
            if sent:
                sentences.append(sent)
            last_idx = end_idx

        if last_idx < len(text):
            tail = text[last_idx:].strip()
            if tail:
                sentences.append(tail)

        if not sentences:
            sentences = [text]

        chunks: List[str] = []
        curr_sents: List[str] = []
        curr_len = 0
        i = 0
        n = len(sentences)

        while i < n:
            sent = sentences[i]
            sent_len = len(sent)

            if sent_len > max_chars:
                if curr_sents:
                    chunks.append(" ".join(curr_sents))
                    curr_sents = []
                    curr_len = 0

                sub_start = 0
                step = max_chars - overlap if max_chars > overlap else max_chars
                while sub_start < sent_len:
                    sub_end = sub_start + max_chars
                    sub_chunk = sent[sub_start:sub_end].strip()
                    if sub_chunk:
                        chunks.append(sub_chunk)
                    sub_start += step
                
                i += 1
                continue

            if curr_len + sent_len <= max_chars:
                curr_sents.append(sent)
                curr_len += sent_len + 1
                i += 1
            else:
                if curr_sents:
                    chunks.append(" ".join(curr_sents))

                overlap_len = 0
                overlap_sents: List[str] = []
                for prev_sent in reversed(curr_sents):
                    if overlap_len + len(prev_sent) <= overlap:
                        overlap_sents.insert(0, prev_sent)
                        overlap_len += len(prev_sent) + 1
                    else:
                        break

                curr_sents = overlap_sents
                curr_len = sum(len(s) + 1 for s in curr_sents)

                if curr_len + sent_len > max_chars:
                    curr_sents = []
                    curr_len = 0

        if curr_sents:
            chunks.append(" ".join(curr_sents))

        return chunks


# ==============================================================================
# 4. 문서 타입별 청킹 엔진 (LegalChunkerV4)
# ==============================================================================
class LegalChunkerV4:
    """법률 데이터 4종(법령, 판례, 해석례, 용어) 청킹 비즈니스 로직."""

    @classmethod
    def _safe_str_at(cls, data_list: Any, index: int) -> str:
        """리스트 또는 단일 객체에서 안전하게 인덱스별 문자열 추출."""
        if isinstance(data_list, list):
            if 0 <= index < len(data_list):
                val = data_list[index]
                return str(val) if val is not None else ""
            return ""
        return str(data_list) if data_list is not None else ""

    @classmethod
    def chunk_law(cls, data: Dict[str, Any]) -> List[LegalChunk]:
        """법령 원본 JSON을 조문 및 부칙 청크로 변환."""
        chunks: List[LegalChunk] = []
        if not isinstance(data, dict):
            return chunks

        law_info = data.get("법령") or {}
        base_info = law_info.get("기본정보") or {}

        law_id = str(base_info.get("법령ID", ""))
        law_name = str(base_info.get("법령명_한글", ""))
        effective_date = str(base_info.get("시행일자", ""))

        # 조문 처리
        articles = (law_info.get("조문") or {}).get("조문단위", [])
        if isinstance(articles, dict):
            articles = [articles]

        for art in articles:
            if not isinstance(art, dict):
                continue

            art_flag = art.get("조문여부")
            if art_flag not in (None, "", "조문"):
                continue

            art_no = str(art.get("조문번호", ""))
            branch_no = str(art.get("조문가지번호", ""))
            art_title = str(art.get("조문제목") or "").strip()
            has_branch = bool(branch_no) and branch_no != "0"
            art_key = art.get("조문키") or f"{art_no}{'_' + branch_no if has_branch else ''}"

            raw_content = TextCleaner.normalize_whitespace(art.get("조문내용", ""))
            art_label = f"제{art_no}조의{branch_no}" if has_branch else f"제{art_no}조"
            title_str = f"{art_label}({art_title})" if art_title else art_label
            is_deleted = bool(DELETED_ARTICLE_PATTERN.search(raw_content))

            # 항/호 구조 파싱
            items = art.get("항") or []
            if isinstance(items, dict):
                items = [items]

            hier_lines: List[str] = []
            first_child_text: Optional[str] = None
            for item in items:
                if not isinstance(item, dict):
                    continue

                hang_content = item.get("항내용")
                if hang_content:
                    hier_lines.append(TextCleaner.normalize_whitespace(hang_content))
                    if first_child_text is None:
                        first_child_text = str(hang_content)

                sub_items = item.get("호") or []
                if isinstance(sub_items, dict):
                    sub_items = [sub_items]
                for sub in sub_items:
                    if isinstance(sub, dict) and sub.get("호내용"):
                        hier_lines.append(f"  {TextCleaner.normalize_whitespace(sub['호내용'])}")
                        if first_child_text is None:
                            first_child_text = str(sub["호내용"])

            # 내용 중복 결합 방지 로직
            text_builder: List[str] = []
            if items:
                clean_probe_text = ITEM_SYMBOL_PATTERN.sub('', first_child_text or "").strip()
                probe = clean_probe_text[:DUP_PROBE_LEN]
                is_duplicated = len(probe) >= DUP_PROBE_MIN_LEN and probe in raw_content

                text_builder.append(title_str if is_duplicated else (raw_content or title_str))
                text_builder.extend(hier_lines)
            else:
                text_builder.append(raw_content if raw_content else title_str)

            full_content = "\n".join(text_builder)

            chunks.append(LegalChunk(
                chunk_id=f"law_{law_id}_art_{art_key}",
                doc_type="law",
                doc_id=law_id,
                title=f"{law_name} {title_str}",
                content=full_content,
                metadata={
                    "doc_type": "law",
                    "law_id": law_id,
                    "law_name": law_name,
                    "article_no": art_no,
                    "article_branch_no": branch_no if has_branch else None,
                    "article_title": art_title,
                    "effective_date": effective_date,
                    "is_deleted": is_deleted
                }
            ))

        # 부칙 처리
        addendums = (law_info.get("부칙") or {}).get("부칙단위", [])
        if isinstance(addendums, dict):
            addendums = [addendums]

        for add in addendums:
            if not isinstance(add, dict):
                continue

            add_key = str(add.get("부칙키", ""))
            add_content_list = add.get("부칙내용") or []

            flat_text: List[str] = []
            if isinstance(add_content_list, list):
                for lines in add_content_list:
                    if isinstance(lines, list):
                        flat_text.extend([TextCleaner.normalize_whitespace(l) for l in lines])
                    elif isinstance(lines, str):
                        flat_text.append(TextCleaner.normalize_whitespace(lines))
            elif isinstance(add_content_list, str):
                flat_text.append(TextCleaner.normalize_whitespace(add_content_list))

            chunks.append(LegalChunk(
                chunk_id=f"law_{law_id}_add_{add_key}",
                doc_type="addendum",
                doc_id=law_id,
                title=f"{law_name} 부칙 (공포번호: {add.get('부칙공포번호', '')})",
                content="\n".join(flat_text),
                metadata={
                    "doc_type": "addendum",
                    "law_id": law_id,
                    "addendum_key": add_key,
                    "promulgation_no": str(add.get("부칙공포번호", "")),
                    "promulgation_date": str(add.get("부칙공포일자", ""))
                }
            ))

        return chunks

    @classmethod
    def chunk_prec(cls, data: Dict[str, Any]) -> List[LegalChunk]:
        """판례 원본 JSON을 Head(요약) 및 Body(본문) 청크로 변환."""
        chunks: List[LegalChunk] = []
        if not isinstance(data, dict):
            return chunks

        prec = data.get("PrecService") or {}
        prec_id = str(prec.get("판례정보일련번호", ""))
        case_no = str(prec.get("사건번호", ""))
        case_name = str(prec.get("사건명", ""))

        meta_base = {
            "doc_type": "prec",
            "case_no": case_no,
            "court_name": str(prec.get("법원명", "")),
            "sentencing_date": str(prec.get("선고일자", "")),
            "case_type": str(prec.get("사건종류명", "")),
            "ref_articles": TextCleaner.parse_ref_articles(prec.get("참조조문"))
        }

        # 1. Head (판시사항 + 판결요지)
        holding = TextCleaner.strip_html(prec.get("판시사항"))
        summary = TextCleaner.strip_html(prec.get("판결요지"))
        head_text = f"사건: {case_name} ({case_no})\n[판시사항]\n{holding}\n[판결요지]\n{summary}"

        if len(head_text) <= 1500:
            chunks.append(LegalChunk(
                chunk_id=f"prec_{prec_id}_head",
                doc_type="prec",
                doc_id=prec_id,
                title=f"판례 [{case_no}] {case_name} (요약)",
                content=head_text,
                metadata={**meta_base, "chunk_role": "head"}
            ))
        else:
            head_chunks = TextCleaner.split_by_sentence_boundary(head_text, max_chars=1200, overlap=200)
            for idx, h_text in enumerate(head_chunks):
                chunks.append(LegalChunk(
                    chunk_id=f"prec_{prec_id}_head_{idx+1}",
                    doc_type="prec",
                    doc_id=prec_id,
                    title=f"판례 [{case_no}] {case_name} (요약-{idx+1})",
                    content=h_text,
                    metadata={**meta_base, "chunk_role": "head", "chunk_index": idx + 1}
                ))

        # 2. Body (판례내용)
        body_text = TextCleaner.strip_html(prec.get("판례내용"))
        if body_text:
            body_chunks = TextCleaner.split_by_sentence_boundary(body_text, max_chars=1200, overlap=200)
            for idx, b_text in enumerate(body_chunks):
                chunks.append(LegalChunk(
                    chunk_id=f"prec_{prec_id}_body_{idx+1}",
                    doc_type="prec",
                    doc_id=prec_id,
                    title=f"판례 [{case_no}] {case_name} (본문-{idx+1})",
                    content=f"사건: {case_name} ({case_no})\n[판례 내용]\n{b_text}",
                    metadata={**meta_base, "chunk_role": "body", "chunk_index": idx + 1}
                ))

        return chunks

    @classmethod
    def chunk_expc(cls, data: Dict[str, Any]) -> List[LegalChunk]:
        """법령해석례 원본 JSON을 질의, 회답, 이유 청크로 변환."""
        chunks: List[LegalChunk] = []
        if not isinstance(data, dict):
            return chunks

        expc = data.get("ExpcService") or {}
        expc_id = str(expc.get("법령해석례일련번호", ""))
        agenda_name = str(expc.get("안건명", ""))
        agenda_no = str(expc.get("안건번호", ""))

        meta_base = {
            "doc_type": "expc",
            "case_no": agenda_no,
            "interpreting_agency": str(expc.get("해석기관명", "")),
            "requesting_agency": str(expc.get("질의기관명", "")),
            "interpretation_date": str(expc.get("해석일자", ""))
        }

        # 회답
        answer = TextCleaner.strip_html(expc.get("회답"))
        if answer:
            chunks.append(LegalChunk(
                chunk_id=f"expc_{expc_id}_conclusion",
                doc_type="expc",
                doc_id=expc_id,
                title=f"법령해석례: {agenda_name} (회답)",
                content=f"안건명: {agenda_name}\n[회답]\n{answer}",
                metadata={**meta_base, "chunk_role": "conclusion"}
            ))

        # 질의요지
        question = TextCleaner.strip_html(expc.get("질의요지"))
        if question:
            chunks.append(LegalChunk(
                chunk_id=f"expc_{expc_id}_question",
                doc_type="expc",
                doc_id=expc_id,
                title=f"법령해석례: {agenda_name} (질의요지)",
                content=f"안건명: {agenda_name}\n[질의요지]\n{question}",
                metadata={**meta_base, "chunk_role": "question"}
            ))

        # 이유 (단락 기호 '○' 분할)
        raw_reasoning = TextCleaner.strip_html(expc.get("이유"))
        if raw_reasoning:
            paragraphs = [p.strip() for p in raw_reasoning.split("○") if p.strip()]
            chunk_idx = 1
            for p in paragraphs:
                if len(p) <= 1800:
                    chunks.append(LegalChunk(
                        chunk_id=f"expc_{expc_id}_reasoning_{chunk_idx}",
                        doc_type="expc",
                        doc_id=expc_id,
                        title=f"법령해석례: {agenda_name} (이유-{chunk_idx})",
                        content=f"안건명: {agenda_name}\n[이유]\n○ {p}",
                        metadata={**meta_base, "chunk_role": "reasoning", "paragraph_index": chunk_idx}
                    ))
                    chunk_idx += 1
                else:
                    sub_chunks = TextCleaner.split_by_sentence_boundary(p, max_chars=1200, overlap=150)
                    for sub_p in sub_chunks:
                        chunks.append(LegalChunk(
                            chunk_id=f"expc_{expc_id}_reasoning_{chunk_idx}",
                            doc_type="expc",
                            doc_id=expc_id,
                            title=f"법령해석례: {agenda_name} (이유-{chunk_idx})",
                            content=f"안건명: {agenda_name}\n[이유]\n{sub_p}",
                            metadata={**meta_base, "chunk_role": "reasoning", "paragraph_index": chunk_idx}
                        ))
                        chunk_idx += 1

        return chunks

    @classmethod
    def chunk_lstrm(cls, data: Dict[str, Any]) -> List[LegalChunk]:
        """법령용어 원본 JSON을 청크로 변환 (스칼라 및 컬럼형 리스트 데이터 자동 Unrolling 지원)."""
        chunks: List[LegalChunk] = []
        if not isinstance(data, dict):
            return chunks

        lstrm = data.get("LsTrmService") or {}
        if not isinstance(lstrm, dict) or not lstrm:
            return chunks

        raw_ids = lstrm.get("법령용어일련번호")
        raw_names = lstrm.get("법령용어명_한글")
        raw_defs = lstrm.get("법령용어정의")
        raw_sources = lstrm.get("출처")

        # 1. 컬럼형 리스트(Columnar JSON) 형태로 들어온 경우 처리
        if isinstance(raw_ids, list):
            for i in range(len(raw_ids)):
                term_id = cls._safe_str_at(raw_ids, i).strip()
                term_name = TextCleaner.strip_html(cls._safe_str_at(raw_names, i))
                definition = TextCleaner.strip_html(cls._safe_str_at(raw_defs, i))
                source = TextCleaner.strip_html(cls._safe_str_at(raw_sources, i))

                if not term_id and not term_name:
                    continue

                has_pure_korean = bool(PURE_KOREAN_PATTERN.search(term_name))
                chunks.append(LegalChunk(
                    chunk_id=f"lstrm_{term_id}" if term_id else f"lstrm_unk_{i}",
                    doc_type="lstrm",
                    doc_id=term_id,
                    title=f"법령용어: {term_name}",
                    content=f"용어: {term_name}\n정의: {definition}\n출처: {source}",
                    metadata={
                        "doc_type": "lstrm",
                        "term_id": term_id,
                        "term_name": term_name,
                        "source_regulation": source,
                        "name_missing_flag": not has_pure_korean
                    }
                ))
        # 2. 단일 스칼라 객체 형태로 들어온 경우 처리
        else:
            term_id = str(raw_ids or "").strip()
            term_name = TextCleaner.strip_html(raw_names)
            definition = TextCleaner.strip_html(raw_defs)
            source = TextCleaner.strip_html(raw_sources)

            if term_id or term_name:
                has_pure_korean = bool(PURE_KOREAN_PATTERN.search(term_name))
                chunks.append(LegalChunk(
                    chunk_id=f"lstrm_{term_id}",
                    doc_type="lstrm",
                    doc_id=term_id,
                    title=f"법령용어: {term_name}",
                    content=f"용어: {term_name}\n정의: {definition}\n출처: {source}",
                    metadata={
                        "doc_type": "lstrm",
                        "term_id": term_id,
                        "term_name": term_name,
                        "source_regulation": source,
                        "name_missing_flag": not has_pure_korean
                    }
                ))

        return chunks


# ==============================================================================
# 5. 파이프라인 관리자 및 로더 (Pipeline Orchestrator)
# ==============================================================================
def load_json_records(file_path: Path) -> List[Dict[str, Any]]:
    """대용량 JSON 파일에서 루트 레코드 배열을 안전하게 로드."""
    if not file_path.exists():
        logger.warning(f"⚠️ 파일이 존재하지 않아 건너뜁니다: {file_path}")
        return []

    size_mb = file_path.stat().st_size / (1024 * 1024)
    logger.info(f"📂 JSON 데이터 로드 시작: {file_path.name} ({size_mb:.1f} MB)")

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            for filename_key in ["body_DB01_law.json", "body_DB03_expc.json", "body_DB10_lstrm.json", "body_DB19_prec.json"]:
                if filename_key in data:
                    data = data[filename_key]
                    break

        if isinstance(data, dict):
            return [data]
        elif isinstance(data, list):
            return data
        return []

    except Exception as e:
        logger.error(f"❌ JSON 로딩 실패 [{file_path.name}]: {e}", exc_info=True)
        return []


def run_pipeline(raw_dir: Path, processed_dir: Path) -> PipelineMetrics:
    """배치 파이프라인 총괄 실행 함수."""
    metrics = PipelineMetrics()
    processed_dir.mkdir(parents=True, exist_ok=True)

    chunk_file_path = processed_dir / f"chunks_v{VERSION}.jsonl"
    dlq_file_path = processed_dir / f"dlq_v{VERSION}.jsonl"

    logger.info(f"🚀 법률 RAG 데이터 청킹 파이프라인(v{VERSION})을 시작합니다.")

    with open(chunk_file_path, "w", encoding="utf-8") as f_chunk, \
         open(dlq_file_path, "w", encoding="utf-8") as f_dlq:

        # ----------------------------------------------------------------------
        # Step 1. 판례 Index 매핑 테이블 구성
        # ----------------------------------------------------------------------
        prec_idx_list = load_json_records(raw_dir / "idx_DB19_prec.json")
        idx_map: Dict[str, Dict[str, Any]] = {}
        for idx_item in prec_idx_list:
            if isinstance(idx_item, dict) and idx_item.get("판례일련번호"):
                idx_map[str(idx_item["판례일련번호"])] = idx_item
        logger.info(f"✅ 판례 인덱스 매핑 테이블 구축 완료 (총 {len(idx_map):,} 건)")

        # ----------------------------------------------------------------------
        # Step 2. 법령 (body_DB01_law.json)
        # ----------------------------------------------------------------------
        laws = load_json_records(raw_dir / "body_DB01_law.json")
        for item in laws:
            try:
                for chunk in LegalChunkerV4.chunk_law(item):
                    f_chunk.write(json.dumps(chunk.to_dict(), ensure_ascii=False) + "\n")
                    metrics.record_chunk(chunk.doc_type)
            except Exception as e:
                dlq = DLQItem("body_DB01_law.json", "N/A", str(e), item if isinstance(item, dict) else {})
                f_dlq.write(json.dumps(dlq.to_dict(), ensure_ascii=False) + "\n")
                metrics.record_dlq()

        logger.info(f"✅ [법령] 총 {len(laws):,} 건 처리 완료")

        # ----------------------------------------------------------------------
        # Step 3. 판례 (body_DB19_prec.json) - Alignment 검수 포함
        # ----------------------------------------------------------------------
        precs = load_json_records(raw_dir / "body_DB19_prec.json")
        for item in precs:
            try:
                if not isinstance(item, dict):
                    continue

                prec_service = item.get("PrecService") or {}
                prec_id = str(prec_service.get("판례정보일련번호", ""))

                if not prec_id:
                    dlq = DLQItem("body_DB19_prec.json", "N/A", "판례정보일련번호 키 누락", item)
                    f_dlq.write(json.dumps(dlq.to_dict(), ensure_ascii=False) + "\n")
                    metrics.record_dlq()
                elif prec_id not in idx_map:
                    dlq = DLQItem("body_DB19_prec.json", prec_id, "idx_DB19_prec 매핑 실패", item)
                    f_dlq.write(json.dumps(dlq.to_dict(), ensure_ascii=False) + "\n")
                    metrics.record_dlq()
                else:
                    for chunk in LegalChunkerV4.chunk_prec(item):
                        f_chunk.write(json.dumps(chunk.to_dict(), ensure_ascii=False) + "\n")
                        metrics.record_chunk(chunk.doc_type)

            except Exception as e:
                dlq = DLQItem("body_DB19_prec.json", "ERROR", str(e), item if isinstance(item, dict) else {})
                f_dlq.write(json.dumps(dlq.to_dict(), ensure_ascii=False) + "\n")
                metrics.record_dlq()

        logger.info(f"✅ [판례] 총 {len(precs):,} 건 처리 완료")

        # ----------------------------------------------------------------------
        # Step 4. 법령해석례 (body_DB03_expc.json)
        # ----------------------------------------------------------------------
        expcs = load_json_records(raw_dir / "body_DB03_expc.json")
        for item in expcs:
            try:
                for chunk in LegalChunkerV4.chunk_expc(item):
                    f_chunk.write(json.dumps(chunk.to_dict(), ensure_ascii=False) + "\n")
                    metrics.record_chunk(chunk.doc_type)
            except Exception as e:
                dlq = DLQItem("body_DB03_expc.json", "N/A", str(e), item if isinstance(item, dict) else {})
                f_dlq.write(json.dumps(dlq.to_dict(), ensure_ascii=False) + "\n")
                metrics.record_dlq()

        logger.info(f"✅ [법령해석례] 총 {len(expcs):,} 건 처리 완료")

        # ----------------------------------------------------------------------
        # Step 5. 법령용어 (body_DB10_lstrm.json)
        # ----------------------------------------------------------------------
        lstrms = load_json_records(raw_dir / "body_DB10_lstrm.json")
        for item in lstrms:
            try:
                for chunk in LegalChunkerV4.chunk_lstrm(item):
                    f_chunk.write(json.dumps(chunk.to_dict(), ensure_ascii=False) + "\n")
                    metrics.record_chunk(chunk.doc_type)
            except Exception as e:
                dlq = DLQItem("body_DB10_lstrm.json", "N/A", str(e), item if isinstance(item, dict) else {})
                f_dlq.write(json.dumps(dlq.to_dict(), ensure_ascii=False) + "\n")
                metrics.record_dlq()

        logger.info(f"✅ [법령용어] 총 {len(lstrms):,} 건 처리 완료")

    return metrics


# ==============================================================================
# 6. 진입점 (Main Entry Point)
# ==============================================================================
def main():
    """메인 실행 진입점."""
    base_dir = Path(__file__).resolve().parent.parent
    raw_data_dir = base_dir / "data" / "raw"
    processed_data_dir = base_dir / "data" / "processed"

    metrics = run_pipeline(raw_data_dir, processed_data_dir)
    logger.info(metrics.get_summary())


if __name__ == "__main__":
    main()