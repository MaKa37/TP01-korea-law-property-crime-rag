"""
src/ingest_full_documents_DB19.py
=================================
판례 JSON 원본 데이터를 읽어 `legal_documents` 테이블에 원문 전체를 벌크 적재합니다.
"""

import json
import logging
import os
import re
from pathlib import Path
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DATA_PATH = Path("data") / "raw" / "body_DB19_prec.json"

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", 5432))
DB_NAME = os.getenv("DB_NAME", "legal_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")


def clean_text(text: str) -> str:
    """HTML 줄바꿈 태그 정리 및 양쪽 공백 제거"""
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", str(text), flags=re.IGNORECASE)
    return text.strip()


def ingest_full_documents():
    if not DATA_PATH.exists():
        logger.error(f"❌ 파일을 찾을 수 없습니다: {DATA_PATH}")
        return

    logger.info(f"📂 JSON 파일 로딩 시작: {DATA_PATH}")
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 1) JSON 구조 파싱
    raw_records = []
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, dict) and "PrecService" in value:
                raw_records.append(value["PrecService"])
            elif key == "PrecService" and isinstance(value, dict):
                raw_records.append(value)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and "PrecService" in item:
                raw_records.append(item["PrecService"])
            else:
                raw_records.append(item)

    total_count = len(raw_records)
    logger.info(f"총 {total_count}개의 판례 원문 데이터를 추출하여 적재를 시작합니다.")

    query = """
        INSERT INTO legal_documents (
            prec_id, case_number, doc_type, title, court_name, 
            issue_date, case_type, ref_articles, ref_precedents, full_text
        )
        VALUES %s
        ON CONFLICT (prec_id) DO UPDATE SET
            case_number = EXCLUDED.case_number,
            title = EXCLUDED.title,
            court_name = EXCLUDED.court_name,
            issue_date = EXCLUDED.issue_date,
            case_type = EXCLUDED.case_type,
            ref_articles = EXCLUDED.ref_articles,
            ref_precedents = EXCLUDED.ref_precedents,
            full_text = EXCLUDED.full_text;
    """

    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )
        cursor = conn.cursor()

        batch_size = 1000
        batch_data = []
        inserted_count = 0

        for r in raw_records:
            prec_id = str(r.get("판례정보일련번호", "")).strip()
            case_number = str(r.get("사건번호", "")).strip()
            
            # 고유 식별자가 없으면 사건번호 대체, 둘 다 없으면 스킵
            if not prec_id:
                prec_id = case_number
            if not prec_id:
                continue

            title = clean_text(r.get("사건명", "제목없음"))
            court_name = clean_text(r.get("법원명", ""))
            issue_date = clean_text(r.get("선고일자", ""))
            case_type = clean_text(r.get("사건종류명", ""))
            ref_articles = clean_text(r.get("참조조문", ""))
            ref_precedents = clean_text(r.get("참조판례", ""))

            point = clean_text(r.get("판시사항", ""))
            summary = clean_text(r.get("판결요지", ""))
            body = clean_text(r.get("판례내용", ""))

            # 구조화된 원문 텍스트 생성
            full_text_parts = []
            if point:
                full_text_parts.append(f"【판시사항】\n{point}")
            if summary:
                full_text_parts.append(f"【판결요지】\n{summary}")
            if ref_articles:
                full_text_parts.append(f"【참조조문】\n{ref_articles}")
            if ref_precedents:
                full_text_parts.append(f"【참조판례】\n{ref_precedents}")
            if body:
                full_text_parts.append(f"【판례내용】\n{body}")

            full_text = "\n\n".join(full_text_parts).strip()

            batch_data.append((
                prec_id,
                case_number,
                "판례",
                title,
                court_name,
                issue_date,
                case_type,
                ref_articles,
                ref_precedents,
                full_text
            ))

            # 1,000건마다 DB에 밀어 넣고 메모리 비우기
            if len(batch_data) >= batch_size:
                execute_values(cursor, query, batch_data, page_size=batch_size)
                conn.commit()
                inserted_count += len(batch_data)
                logger.info(f"🚀 적재 진행 중: {inserted_count}/{total_count} 건 완료...")
                batch_data = []

        # 남은 잔여 데이터 처리
        if batch_data:
            execute_values(cursor, query, batch_data, page_size=batch_size)
            conn.commit()
            inserted_count += len(batch_data)

        logger.info(f"🎉 모든 판례 원문 적재 완료! (총 {inserted_count}건)")

    except Exception as e:
        logger.error(f"🚨 DB 적재 실패: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            cursor.close()
            conn.close()


if __name__ == "__main__":
    ingest_full_documents()