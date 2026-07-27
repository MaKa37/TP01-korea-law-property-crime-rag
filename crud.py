from uuid import UUID
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from models import PipelineJob, LegalDoc, LegalChunk

async def create_pipeline_job(db: AsyncSession, target_type: str, target_id: str) -> PipelineJob:
    """새로운 데이터 수집 작업을 생성합니다."""
    job = PipelineJob(target_type=target_type, target_id=target_id, status="PENDING")
    db.add(job)
    # flush()를 통해 DB에 쿼리를 전송하여 ID를 할당받고 제약조건(IntegrityError)을 확인합니다.
    await db.flush()
    return job

async def update_job_status(db: AsyncSession, job_id: UUID, status: str, error_log: str = None):
    """작업의 진행 상태 및 에러 로그를 업데이트합니다."""
    stmt = update(PipelineJob).where(PipelineJob.job_id == job_id).values(status=status)
    
    if error_log:
        stmt = stmt.values(error_log=error_log)
        
    await db.execute(stmt)

async def insert_legal_doc(db: AsyncSession, target_id: str, title: str) -> UUID:
    """법령 원문 메타데이터를 저장하고 문서 ID를 반환합니다."""
    doc = LegalDoc(target_id=target_id, title=title)
    db.add(doc)
    await db.flush()
    return doc.doc_id

async def insert_legal_chunks(db: AsyncSession, doc_id: UUID, chunks: list[str], embeddings: list[list[float]]):
    """청크 텍스트와 임베딩 벡터를 일괄(Bulk) 저장합니다."""
    # 청크 리스트와 임베딩 리스트의 크기가 동일해야 합니다.
    chunk_objects = [
        LegalChunk(doc_id=doc_id, content=chunk, embedding=emb)
        for chunk, emb in zip(chunks, embeddings)
    ]
    
    db.add_all(chunk_objects)
    await db.flush()