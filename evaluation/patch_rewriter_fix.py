"""
evaluation/patch_rewriter_fix.py
================================
rag/query_rewriter.py의 401 인증 오류를 수정하고 은어 처리 프롬프트를 고도화합니다.
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

load_dotenv()
logger = logging.getLogger(__name__)

def expand_query(query: str, config=None) -> str:
    \"\"\"
    LLM을 사용하여 원본 질의를 검색용 법률 키워드 모음으로 재작성합니다.
    \"\"\"
    # [수정됨] NVIDIA NIM API 키를 Config 객체나 환경변수에서 올바르게 가져옵니다.
    api_key = getattr(config, "nim_api_key", os.getenv("NVIDIA_NIM_API_KEY"))
    if not api_key:
        logger.error("NVIDIA NIM API 키를 찾을 수 없습니다.")
        return query
        
    base_url = getattr(config, "llm_base_url", "https://integrate.api.nvidia.com/v1")
    model = getattr(config, "llm_model_name", "meta/llama-3.1-70b-instruct")

    try:
        client = OpenAI(base_url=base_url, api_key=api_key)
        
        prompt = (
            "당신은 한국 법률 RAG 시스템의 검색어 최적화(Query Rewriter) AI입니다.\\n"
            "사용자의 일상적인 질문을 분석하여, 판례 및 법령 검색에 최적화된 법률 용어와 동의어가 포함된 '검색어 모음'으로 재작성하세요.\\n\\n"
            "[지침]\\n"
            "1. 문장이 아닌, 띄어쓰기로 구분된 핵심 키워드 형태로 출력하세요.\\n"
            "2. 원본 질문의 핵심 단어를 포함하되, 인터넷 은어(예: 벽돌 -> 사기, 먹튀 -> 편취 등)는 법률 용어로 치환하세요.\\n"
            "3. 관련된 법률 용어(예: 사기 -> 기망행위, 재물 편취, 불법영득의사, 고지의무 등)를 적극 추가하세요.\\n"
            "4. 부가적인 설명 없이 오직 재작성된 키워드만 출력하세요.\\n\\n"
            f"[원본 질문] {query}\\n"
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
    print("✅ rag/query_rewriter.py 수정 완료! (401 에러 픽스 및 프롬프트 개선)")

if __name__ == "__main__":
    main()