import os
import httpx
import xml.etree.ElementTree as ET
from fastapi import FastAPI, BackgroundTasks, Depends
from tenacity import retry, stop_after_attempt, wait_exponential
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from database import async_session_maker, get_db
from crud import insert_raw_xml, get_unprocessed_raw_data, mark_raw_data_processed, insert_legal_doc, insert_legal_chunks
# from parsers import parse_universal_xml  <-- 이전 문서의 파싱 로직 모듈화 가정

app = FastAPI(title="2-Stage Legal RAG Pipeline")

LAW_API_KEY = os.getenv("LAW_OPEN_API_KEY")
NVIDIA_API_KEY = os.getenv("NVIDIA_NIM_API_KEY")
TARGET_MAPPING = {"STATUTE": "law", "PRECEDENT": "prec", "EXPLANATION": "exp", "LIFELAW": "lifelaw", "TERM": "lstrm", "ASSET": "lsByl"}

# ==========================================
# Phase 1: API 호출 및 원본 데이터 보관 (직렬화)
# ==========================================
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def fetch_law_data(target_type: str, target_id: str) -> str:
    api_target = TARGET_MAPPING.get(target_type.upper())
    url = f"http://www.law.go.kr/DRF/lawService.do?OC={LAW_API_KEY}&target={api_target}&MST={target_id}&type=XML"
    async with httpx.AsyncClient() as client:
        response = await client.get(url, timeout=10.0)
        response.raise_for_status()
        return response.text

async def phase1_fetch_and_store(target_type: str, target_id: str):
    async with async_session_maker() as db:
        try:
            xml_text = await fetch_law_data(target_type, target_id)
            await insert_raw_xml(db, target_type, target_id, xml_text)
            await db.commit()
        except IntegrityError:
            pass # 이미 오늘자 스냅샷으로 확보된 경우 무시
        except Exception as e:
            print(f"Fetch failed for {target_id}: {e}")

@app.post("/api/v1/ingest/fetch")
async def trigger_phase1_fetch(target_type: str, target_id: str, background_tasks: BackgroundTasks):
    """트래픽 차단 우회를 위해 공공 API에서 XML만 다운로드하여 로컬 Vault에 적재합니다."""
    background_tasks.add_task(phase1_fetch_and_store, target_type, target_id)
    return {"status": "Phase 1 started", "message": f"{target_id} 원본 다운로드 예약"}

# ==========================================
# Phase 2: 내부 DB 기반 파싱 및 임베딩 (안전한 일괄 처리)
# ==========================================
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def get_nv_embeddings(texts: list[str]) -> list[list[float]]:
    url = "https://integrate.api.nvidia.com/v1/embeddings"
    headers = {"Authorization": f"Bearer {NVIDIA_API_KEY}", "Content-Type": "application/json"}
    payload = {"input": texts, "model": "nvidia/llama-3.2-nv-embedqa-1b-v2", "encoding_format": "float", "dimensions": 1024}
    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload, timeout=30.0)
        response.raise_for_status()
        return [item["embedding"] for item in response.json()["data"]]

async def phase2_process_embeddings():
    async with async_session_maker() as db:
        # DB에서 미처리 원본 데이터 최대 50건씩 가져오기 (API Limit 걱정 없음)
        raw_records = await get_unprocessed_raw_data(db, limit=50)
        
        for record in raw_records:
            try:
                # 1. 전처리 및 정규화 (CDATA 클린징 및 조문 단위 청킹)
                doc_title, chunks = parse_universal_xml(record.raw_xml.encode('utf-8'), record.target_type)
                if not chunks:
                    await mark_raw_data_processed(db, record.raw_id)
                    continue

                # 2. 임베딩 모델 호출 (NVIDIA NIM)
                embeddings = await get_nv_embeddings(chunks)
                
                # 3. Vector DB 적재 및 Phase 2 완료 마킹
                async with db.begin():
                    doc_id = await insert_legal_doc(db, record.target_id, doc_title)
                    await insert_legal_chunks(db, doc_id, chunks, embeddings)
                    await mark_raw_data_processed(db, record.raw_id)
                    
            except Exception as e:
                print(f"Processing failed for Record ID {record.raw_id}: {e}")

@app.post("/api/v1/ingest/process")
async def trigger_phase2_process(background_tasks: BackgroundTasks):
    """내부 DB(Raw Vault)에 쌓인 데이터를 읽어 청킹 및 임베딩을 수행합니다."""
    background_tasks.add_task(phase2_process_embeddings)
    return {"status": "Phase 2 started", "message": "로컬 데이터베이스 기반 임베딩 작업 시작"}