import json
import os
import asyncio
import aiohttp
import asyncpg
from dotenv import load_dotenv

# ==========================================
# 1. 환경 설정 (.env 연동)
# ==========================================
load_dotenv()

DB_CONFIG = {
    "database": os.getenv("DB_NAME", "postgres"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "00000000"),
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": os.getenv("DB_PORT", "5432")
}

NVIDIA_NIM_API_KEY = os.getenv("NVIDIA_NIM_API_KEY")
NIM_API_URL = "https://integrate.api.nvidia.com/v1/embeddings" 
EMBEDDING_MODEL = "nvidia/nv-embedqa-e5-v5"

JSONL_FILE_PATH = "./chunks_out/chunks_all.jsonl"
BATCH_SIZE = 50 
MAX_CONCURRENT_TASKS = 5  

# ==========================================
# 2. 헬퍼 함수
# ==========================================
def extract_title(metadata):
    doc_type = metadata.get("doc_type")
    if doc_type == "law":
        law_name = metadata.get("law_name", "알수없는법령")
        article_no = metadata.get("article_no", "")
        return f"{law_name} 제{article_no}조" if article_no else law_name
    elif doc_type == "expc":
        return metadata.get("case_name", "법령해석례")
    elif doc_type == "prec":
        case_name = metadata.get("case_name", "판례")
        case_no = metadata.get("case_no", "")
        return f"{case_name} ({case_no})"
    return "제목 없음"

# ==========================================
# 3. DB에 이미 존재하는 ID 목록 가져오기 (핵심 최적화)
# ==========================================
async def get_existing_chunk_ids(pool):
    """DB를 조회하여 이미 적재된 chunk_id들의 집합(Set)을 반환합니다."""
    async with pool.acquire() as conn:
        records = await conn.fetch("SELECT chunk_id FROM rag_chunks")
        return {record['chunk_id'] for record in records}

# ==========================================
# 4. 비동기 API 호출 함수 (NVIDIA NIM)
# ==========================================
async def get_embeddings_async(session, texts):
    safe_texts = [text[:250] for text in texts]
    headers = {
        "Authorization": f"Bearer {NVIDIA_NIM_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    payload = {
        "input": safe_texts,
        "model": EMBEDDING_MODEL,
        "input_type": "passage",
        "truncate": "END"
    }
    
    retries = 3
    for attempt in range(retries):
        async with session.post(NIM_API_URL, headers=headers, json=payload) as response:
            if response.status == 200:
                data = await response.json()
                return [item["embedding"] for item in data["data"]]
            elif response.status == 429:
                print(f"⚠️ [API 호출 제한] 대기 후 재시도... ({attempt+1}/{retries})")
                await asyncio.sleep(2 ** attempt)
            else:
                error_text = await response.text()
                raise Exception(f"NIM API 에러: {response.status} - {error_text}")
    raise Exception("API 호출 재시도 횟수를 초과했습니다.")

# ==========================================
# 5. 비동기 DB 적재 함수 (pgvector)
# ==========================================
async def insert_chunks_async(pool, records):
    query = """
        INSERT INTO rag_chunks (
            chunk_id, source_type, source_id, section_type, seq_no, 
            title, content_text, metadata, embedding
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::vector)
        ON CONFLICT (chunk_id) DO NOTHING
    """
    async with pool.acquire() as conn:
        await conn.executemany(query, records)

# ==========================================
# 6. 개별 배치 작업 처리 워커(Worker)
# ==========================================
async def process_batch(sem, pool, session, batch_data, batch_index):
    async with sem:
        try:
            texts = [item['content_text'] for item in batch_data]
            embeddings = await get_embeddings_async(session, texts)
            
            records = []
            for item, emb in zip(batch_data, embeddings):
                emb_str = f"[{','.join(map(str, emb))}]"
                records.append((
                    item['chunk_id'], item['source_type'], item['source_id'], 
                    item['section_type'], item['seq_no'], item['title'], 
                    item['content_text'], item['metadata'], emb_str
                ))
            
            await insert_chunks_async(pool, records)
            print(f"🔄 누락분 배치 #{batch_index} ({len(records)}개) 처리 완료")
            
        except Exception as e:
            print(f"❌ 배치 #{batch_index} 처리 중 에러 발생: {e}")

# ==========================================
# 7. 메인 실행 루프
# ==========================================
async def main():
    if not NVIDIA_NIM_API_KEY:
        print("❌ NVIDIA_NIM_API_KEY가 설정되지 않았습니다.")
        return
    if not os.path.exists(JSONL_FILE_PATH):
        print(f"❌ 파일을 찾을 수 없습니다: {JSONL_FILE_PATH}")
        return

    print("🚀 [시작] 비동기 누락분 데이터 적재 파이프라인")
    
    pool = await asyncpg.create_pool(**DB_CONFIG, min_size=1, max_size=10)
    print("✅ 비동기 DB 커넥션 풀 생성 완료")

    # 🌟 핵심 최적화: 이미 적재된 ID 목록을 가져옵니다.
    existing_ids = await get_existing_chunk_ids(pool)
    print(f"✅ DB에 이미 적재된 데이터 수: {len(existing_ids)}개 (해당 데이터는 건너뜁니다)")

    tasks = []
    sem = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
    
    async with aiohttp.ClientSession() as session:
        batch_data = []
        batch_index = 1
        skipped_count = 0
        
        with open(JSONL_FILE_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                if not line.strip(): continue
                
                chunk_data = json.loads(line)
                chunk_id = chunk_data.get("chunk_id")
                
                # 🌟 DB에 이미 있는 데이터면 파일 읽기 단계에서 바로 건너뜀 (API 호출 X)
                if chunk_id in existing_ids:
                    skipped_count += 1
                    continue
                
                meta = chunk_data.get("metadata", {})
                item = {
                    'chunk_id': chunk_id,
                    'source_type': meta.get("doc_type"),
                    'source_id': str(meta.get("source_sn", "0")),
                    'section_type': meta.get("section_type", "기본"),
                    'seq_no': int(meta.get("seq_no", 0)),
                    'title': extract_title(meta),
                    'content_text': chunk_data.get("chunk_text", ""),
                    'metadata': json.dumps(meta, ensure_ascii=False)
                }
                batch_data.append(item)
                
                if len(batch_data) >= BATCH_SIZE:
                    task = asyncio.create_task(
                        process_batch(sem, pool, session, batch_data.copy(), batch_index)
                    )
                    tasks.append(task)
                    batch_data = []
                    batch_index += 1
            
            if batch_data:
                task = asyncio.create_task(
                    process_batch(sem, pool, session, batch_data, batch_index)
                )
                tasks.append(task)
        
        print(f"⏩ 중복 데이터 {skipped_count}개 스킵 완료.")
        if tasks:
            print(f"⚡ 누락된 {len(tasks)}개의 배치 작업을 병렬로 처리합니다...")
            await asyncio.gather(*tasks)
        else:
            print("✨ 새로 적재할 누락 데이터가 없습니다!")

    await pool.close()
    print("🎉 이삭 줍기 완료 및 DB 커넥션 풀 종료!")

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    asyncio.run(main())