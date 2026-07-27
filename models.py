import uuid
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, ARRAY, Float
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from database import Base

class PipelineJob(Base):
    __tablename__ = "pipeline_jobs"
    
    job_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target_type = Column(String(50), nullable=False)
    # 중복 수집을 방지하기 위해 unique=True 설정 (main.py의 IntegrityError 유발점)
    target_id = Column(String(100), nullable=False, unique=True)
    status = Column(String(20), nullable=False, default="PENDING")
    error_log = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

class LegalDoc(Base):
    __tablename__ = "legal_docs"
    
    doc_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target_id = Column(String(100), nullable=False, unique=True)
    title = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class LegalChunk(Base):
    __tablename__ = "legal_chunks"
    
    chunk_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    doc_id = Column(UUID(as_uuid=True), ForeignKey("legal_docs.doc_id", ondelete="CASCADE"), nullable=False)
    content = Column(Text, nullable=False)
    # pgvector 확장을 사용할 경우 Column(Vector(1024))로 변경 가능. 
    # 기본 PostgreSQL 환경에서는 Float 배열 사용.
    embedding = Column(ARRAY(Float), nullable=False)