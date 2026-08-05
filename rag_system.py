import logging
from typing import List, Dict, Any
from openai import AsyncOpenAI
import asyncpg
from config import config

logger = logging.getLogger("RAGSystem")

class LegalRAGSystem:
    def __init__(self):
        # NVIDIA NIM Async Client
        self.client = AsyncOpenAI(
            api_key=config.NVIDIA_NIM_API_KEY,
            base_url=config.NVIDIA_BASE_URL
        )

    async def _get_embedding(self, text: str) -> List[float]:
        """NVIDIA E5-v5 임베딩 모델 호출 (1536 차원 규격)"""
        try:
            res = await self.client.embeddings.create(
                input=[text],
                model=config.embedding_model
            )
            vec = res.data[0].embedding
            if len(vec) < config.EMBEDDING_DIM:
                vec.extend([0.0] * (config.EMBEDDING_DIM - len(vec)))
            return vec[:config.EMBEDDING_DIM]
        except Exception as e:
            logger.error(f"임베딩 생성 오류: {e}")
            return [0.0] * config.EMBEDDING_DIM

    async def hybrid_search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """로컬 PostgreSQL pgvector (HNSW + PgTrigram) RRF 검색"""
        conn = await asyncpg.connect(config.asyncpg_url)
        try:
            query_vector = await self._get_embedding(query)
            vec_str = "[" + ",".join(map(str, query_vector)) + "]"

            rrf_sql = """
            WITH vector_search AS (
                SELECT chunk_id, title, content, doc_type, metadata,
                       ROW_NUMBER() OVER (ORDER BY embedding <=> $1::vector) AS rank
                FROM legal_chunks
                WHERE embedding IS NOT NULL
                LIMIT 20
            ),
            keyword_search AS (
                SELECT chunk_id, title, content, doc_type, metadata,
                       ROW_NUMBER() OVER (ORDER BY similarity((title || ' ' || content), $2) DESC) AS rank
                FROM legal_chunks
                WHERE (title || ' ' || content) % $2
                LIMIT 20
            )
            SELECT 
                COALESCE(v.chunk_id, k.chunk_id) AS chunk_id,
                COALESCE(v.title, k.title) AS title,
                COALESCE(v.content, k.content) AS content,
                COALESCE(v.doc_type, k.doc_type) AS doc_type,
                COALESCE(v.metadata, k.metadata) AS metadata,
                (COALESCE(1.0 / (60 + v.rank), 0.0) + COALESCE(1.0 / (60 + k.rank), 0.0)) AS rrf_score
            FROM vector_search v
            FULL OUTER JOIN keyword_search k ON v.chunk_id = k.chunk_id
            ORDER BY rrf_score DESC
            LIMIT $3;
            """
            
            rows = await conn.fetch(rrf_sql, vec_str, query, top_k)
            return [dict(row) for row in rows]
        finally:
            await conn.close()

    def build_prompt(self, user_query: str, contexts: List[Dict[str, Any]]) -> str:
        context_str = "\n\n".join([
            f"[{i+1}] {ctx['title']}\n{ctx['content']}" 
            for i, ctx in enumerate(contexts)
        ])

        return f"""
너는 법률 전문가이자 사기·재산범죄 피해자를 돕는 전문 지원 AI이다. 
제공된 참고 법률 및 판례 정보만을 바탕으로, 사용자 피해 상황에 맞춘 체계적인 가이드를 제공하라.

[참고 데이터]
{context_str}

[피해자 질의]
{user_query}

[응답 작성 지침 - 반드시 아래 3단계 구조를 strict하게 지켜 답변할 것]

## ① 관련 법령 및 구성요건 해설
- 형법 제347조(사기) 및 관련 재산범죄 법령을 기준으로 성립 요건(기망행위, 재산적 처분행위, 고의성 등)을 상세히 설명하세요.

## ② 유사 대법원 판례 핵심 요약
- 참고 데이터 중 사용자 피해 상황과 가장 유사한 대법원 판례를 1~2건 선정하여 판시사항과 요지를 요약하세요.

## ③ 피해자 단계별 실천 대응 가이드 (Action Plan)
- **1단계 (증거 수집 및 계좌 동결/조치)**: 카카오톡, 입금 내역, 통화 녹음 등 필수 증거 확보 방법.
- **2단계 (형사 고소)**: 관할 경찰서 방문 및 고소장 작성 핵심 포인트.
- **3단계 (민사상 피해 회복 및 가압류)**: 배상명령 신청, 지급명령, 가압류 등 피해 금액 회수 수단.
"""

    async def answer(self, user_query: str) -> str:
        contexts = await self.hybrid_search(user_query, top_k=config.TOP_K)
        prompt = self.build_prompt(user_query, contexts)

        # Nemotron Ultra 550B 모델 호출
        completion = await self.client.chat.completions.create(
            model=config.chat_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=2048
        )
        return completion.choices[0].message.content