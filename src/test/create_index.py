import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

def create_indexes_via_python():
    print("🚀 인덱스 생성 파이프라인을 시작합니다 (수 분 정도 소요될 수 있습니다)...")
    
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.getenv("DB_NAME", "postgres"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", "postgres")
    )
    conn.autocommit = True  # 인덱스 생성(DDL)을 위해 자동 커밋 활성화
    
    try:
        with conn.cursor() as cur:
            # 1. 넉넉한 작업 메모리 할당 (세션 한정)
            print("1/4. 작업 메모리 할당 중 (8GB)...")
            cur.execute("SET maintenance_work_mem = '8GB';")
            
            # 2. 통계 정보 강제 갱신 (0 bytes 문제 해결)
            print("2/4. 통계 정보 분석(ANALYZE) 중...")
            cur.execute("ANALYZE legal_chunks;")
            
            # 3. Trigram 텍스트 인덱스 생성
            print("3/4. Trigram 인덱스 생성 중 (텍스트 검색 최적화)...")
            cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_legal_chunks_trgm_new 
                ON legal_chunks USING gin ((title || ' ' || content) gin_trgm_ops);
            """)
            
            # 4. Vector 임베딩 HNSW 인덱스 생성
            print("4/4. HNSW 벡터 인덱스 생성 중 (코사인 거리 최적화)...")
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_legal_chunks_embedding_new 
                ON legal_chunks USING hnsw (embedding halfvec_cosine_ops);
            """)
            
            print("✅ 모든 인덱스 생성이 성공적으로 완료되었습니다!")
            
    except Exception as e:
        print(f"❌ 인덱스 생성 중 에러 발생: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    create_indexes_via_python()