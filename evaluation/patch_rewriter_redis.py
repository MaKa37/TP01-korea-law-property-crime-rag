"""
evaluation/patch_rewriter_redis.py
==================================
rag/query_rewriter.py를 수정하여 Redis 대화 기록을 참고하는 기능을 추가합니다.
"""

from pathlib import Path

REWRITER_PATH = Path("rag") / "query_rewriter.py"

def main():
    if not REWRITER_PATH.exists():
        print(f"❌ {REWRITER_PATH} 파일을 찾을 수 없습니다.")
        return

    new_content = """import os
import logging
from openai import OpenAI
from dotenv import load_dotenv
from db.redis_history import RedisChatHistory

load_dotenv()
logger = logging.getLogger(__name__)

def expand_query(query: str, config=None, session_id: str = None) -> str:
    \"\"\"
    LLM을 사용하여 이전 대화 맥락을 참고한 뒤 검색용 법률 키워드로 재작성합니다.
    \"\"\"
    api_key = getattr(config, "nim_api_key", os.getenv("NVIDIA_NIM_API_KEY"))
    if not api_key:
        return query
        
    base_url = getattr(config, "llm_base_url", "https://integrate.api.nvidia.com/v1")
    model = getattr(config, "llm_model_name", "meta/llama-3.1-70b-instruct")

    # 1. Redis에서 이전 대화 기록 가져오기 (세션 ID가 있는 경우)
    chat_context = ""
    if session_id:
        try:
            history_db = RedisChatHistory()
            history = history_db.get_history(session_id)
            if history:
                # 최근 2번의 왕복 대화(최대 4개 메시지)만 요약해서 컨텍스트로 사용
                recent_history = history[-4:]
                context_lines = []
                for h in recent_history:
                    role_name = "사용자" if h["role"] == "user" else "봇"
                    context_lines.append(f"{role_name}: {h['content'][:100]}...") # 토큰 절약을 위해 100자 제한
                chat_context = "\\n".join(context_lines)
        except Exception as e:
            logger.error(f"Redis 컨텍스트 로드 실패: {e}")

    try:
        client = OpenAI(base_url=base_url, api_key=api_key)
        
        prompt = (
            "당신은 한국 법률 RAG 시스템의 검색어 최적화(Query Rewriter) AI입니다.\\n"
            "사용자의 질문을 분석하여, 판례 및 법령 검색에 최적화된 법률 용어와 동의어가 포함된 '검색어 모음'으로 재작성하세요.\\n"
        )
        
        # 2. 이전 대화 맥락이 존재하면 프롬프트에 주입
        if chat_context:
            prompt += (
                f"\\n[이전 대화 맥락]\\n{chat_context}\\n"
                "(위 맥락을 참고하여 현재 질문에 생략된 주어나 지시대명사가 뜻하는 바를 보강하여 키워드를 추출하세요)\\n"
            )
            
        prompt += (
            "\\n[지침]\\n"
            "1. 문장이 아닌, 띄어쓰기로 구분된 핵심 키워드 형태로 출력하세요.\\n"
            "2. 인터넷 은어(예: 벽돌 -> 사기)는 법률 용어로 치환하세요.\\n"
            "3. 관련된 법률 용어(기망행위, 재물 편취 등)를 적극 추가하세요.\\n"
            "4. 부가적인 설명 없이 오직 재작성된 키워드만 출력하세요.\\n\\n"
            f"[현재 질문] {query}\\n"
            "[키워드 모음]:"
        )
        
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=64
        )
        
        expanded = response.choices[0].message.content.strip()
        if expanded.startswith("[키워드 모음]:"):
            expanded = expanded.replace("[키워드 모음]:", "").strip()
            
        return expanded

    except Exception as e:
        logger.error(f"질의 재작성(Rewriting) 실패: {e}")
        return query
"""

    with open(REWRITER_PATH, "w", encoding="utf-8") as f:
        f.write(new_content)
    print("✅ rag/query_rewriter.py 수정 완료! (Redis 컨텍스트 주입 기능 추가)")

if __name__ == "__main__":
    main()