import uuid
from sqlalchemy import Column, String, Text, Boolean, DateTime, ForeignKey, ARRAY, Float
from sqlalchemy.dialects.postgresql import UUID, TSVECTOR
from sqlalchemy.sql import func
from database import Base

class LegalRawVault(Base):
    __tablename__ = "legal_raw_vault"
    raw_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target_type = Column(String(50), nullable=False)
    target_id = Column(String(100), nullable=False, unique=True)
    raw_xml = Column(Text, nullable=False)
    is_processed = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

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
    # content_tsv는 DB에서 자동 생성하므로 ORM에서는 읽기 전용으로 매핑
    embedding = Column(ARRAY(Float), nullable=False) 
