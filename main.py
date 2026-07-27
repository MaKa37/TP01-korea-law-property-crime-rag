import os
import httpx
from uuid import UUID
from fastapi import FastAPI, BackgroundTasks, Depends
from tenacity import retry, stop_after_attempt, wait_exponential
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

# 가상의 모듈 및 의존성 임포트 (실제 환경에 맞게 구현 필요)
from database import async_session_maker, get_db
from crud import update_job_status, insert_legal_doc, insert_legal_chunks, create_pipeline_job

app = FastAPI(title="Legal RAG System")

LAW_API_KEY = os.getenv("LAW_OPEN_API_KEY")
NVIDIA_API_KEY = os.getenv("NVIDIA_NIM_API_KEY")

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def fetch_law_data(target_id: str) -> str:
    """국가법령정보센터 API를 호출하여 법령 XML 데이터를 가져옵니다."""
    url = f"http://www.law.go.kr/DRF/lawService.do?OC={LAW_API_KEY}&target=law&MST={target_id}&type=XML"
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, timeout=10.0)
        response.raise_for_status()
        return response.text

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def get_nv_embeddings(texts: list[str]) -> list[list[float]]:
    """NVIDIA NIM API를 활용하여 텍스트 리스트의 임베딩 벡터를 생성합니다."""
    url = "https://integrate.api.nvidia.com/v1/embeddings"
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "input": texts,
        "model": "nvidia/llama-3.2-nv-embedqa-1b-v2",
        "encoding_format": "float",
        "dimensions": 1024 
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload, timeout=30.0)
        response.raise_for_status()
        data = response.json()
        return [item["embedding"] for item in data["data"]]

async def process_ingestion_task(job_id: UUID, target_id: str):
    """
    백그라운드에서 법령 데이터를 수집하고 임베딩을 생성하여 데이터베이스에 저장합니다.
    FastAPI의 의존성 주입된 세션과 독립적으로 동작하도록 내부에서 세션을 직접 생성합니다.
    """
    async with async_session_maker() as db:
        try:
            # 작업 상태를 진행 중(PROCESSING)으로 변경
            await update_job_status(db, job_id, "PROCESSING")
            await db.commit() 
            
            # 1. 법령 데이터 수집
            xml_data = await fetch_law_data(target_id)
            
            # (임시 하드코딩) 파싱 로직 적용 필요
            doc_title = "형법"
            chunks = ["사람을 기망하여 재물의 교부를 받거나...", "전항의 방법으로 제삼자로 하여금..."]
            
            # 2. 임베딩 벡터 생성
            embeddings = await get_nv_embeddings(chunks)
            
            # 3. 데이터베이스 저장
            # async with db.begin()은 성공 시 자동 commit, 실패 시 자동 rollback을 수행합니다.
            async with db.begin():
                doc_id = await insert_legal_doc(db, target_id, doc_title)
                await insert_legal_chunks(db, doc_id, chunks, embeddings)
                await update_job_status(db, job_id, "SUCCESS")
                
        except Exception as e:
            # db.begin() 블록 내부에서 에러 발생 시 자동 롤백되므로,
            # 상태 변경(FAILED)을 위해 새로운 트랜잭션으로 처리합니다.
            try:
                await update_job_status(db, job_id, "FAILED", error_log=str(e))
                await db.commit()
            except Exception:
                # 상태 업데이트마저 실패할 경우 예외를 삼켜 백그라운드 태스크 크래시를 방지
                pass

@app.post("/api/v1/ingest")
async def trigger_ingestion(
    target_id: str, 
    background_tasks: BackgroundTasks, 
    db: AsyncSession = Depends(get_db)
):
    """
    데이터 수집 파이프라인 작업을 생성하고 백그라운드 태스크로 위임합니다.
    """
    try:
        job = await create_pipeline_job(db, target_type="STATUTE", target_id=target_id)
        await db.commit() 
    except IntegrityError:
        # 중복 데이터 등으로 인한 DB 무결성 에러 발생 시 트랜잭션 롤백
        await db.rollback()
        return {"status": "error", "message": "이미 수집 중이거나 존재하는 데이터입니다."}

    # 세션 의존성 충돌을 방지하기 위해 db 객체는 백그라운드 태스크로 전달하지 않습니다.
    background_tasks.add_task(process_ingestion_task, job.job_id, target_id)
    
    return {
        "status": "accepted", 
        "job_id": job.job_id, 
        "message": "백그라운드에서 수집을 시작합니다."
    }