from uuid import UUID
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from models import LegalRawVault, LegalDoc, LegalChunk

# Phase 1: 원본 저장
async def insert_raw_xml(db: AsyncSession, target_type: str, target_id: str, raw_xml: str) -> UUID:
    raw_data = LegalRawVault(target_type=target_type, target_id=target_id, raw_xml=raw_xml)
    db.add(raw_data)
    await db.flush()
    return raw_data.raw_id

# Phase 2: 미처리 데이터 조회 및 상태 업데이트
async def get_unprocessed_raw_data(db: AsyncSession, limit: int = 50):
    stmt = select(LegalRawVault).where(LegalRawVault.is_processed == False).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()

async def mark_raw_data_processed(db: AsyncSession, raw_id: UUID):
    stmt = update(LegalRawVault).where(LegalRawVault.raw_id == raw_id).values(is_processed=True)
    await db.execute(stmt)

# Phase 2: 정제 데이터 삽입
async def insert_legal_doc(db: AsyncSession, target_id: str, title: str) -> UUID:
    doc = LegalDoc(target_id=target_id, title=title)
    db.add(doc)
    await db.flush()
    return doc.doc_id

async def insert_legal_chunks(db: AsyncSession, doc_id: UUID, chunks: list[str], embeddings: list[list[float]]):
    chunk_objects = [LegalChunk(doc_id=doc_id, content=chunk, embedding=emb) for chunk, emb in zip(chunks, embeddings)]
    db.add_all(chunk_objects)