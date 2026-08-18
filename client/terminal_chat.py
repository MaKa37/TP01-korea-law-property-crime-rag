"""
client/terminal_chat.py
=======================
FastAPI 서버(/chat 엔드포인트)와 통신하여 실시간 스트리밍(SSE) 답변을 받아오는 채팅 클라이언트입니다.
"""

import os
import json
import httpx
from dotenv import load_dotenv

load_dotenv()

# .env에 등록된 API 키 중 첫 번째 키 사용 (없으면 테스트용 임시 키)
API_KEYS = os.getenv("API_KEYS", "test-secret-key-1")
API_KEY = API_KEYS.split(",")[0] if API_KEYS else "test-secret-key-1"

# [수정됨] FastAPI 서버의 APIKeyHeader 이름(X-API-Key)과 일치시킴
HEADERS = {
    "X-API-Key": API_KEY,
    "Content-Type": "application/json"
}

def chat():
    print("=" * 80)
    print("⚖️ 한국 법률 재산범죄 RAG 실시간 채팅 ⚖️")
    print("   - 종료하려면 'quit' 또는 'exit'를 입력하세요.")
    print("=" * 80)
    
    # SSE 스트리밍 통신을 위한 httpx 클라이언트
    with httpx.Client(timeout=120.0) as client:
        while True:
            query = input("\n👤 질문: ").strip()
            if not query:
                continue
            if query.lower() in ['quit', 'exit']:
                print("채팅을 종료합니다.")
                break
            
            print("🤖 봇 응답: ", end="", flush=True)
            
            try:
                # FastAPI 서버로 POST 요청 (스트리밍)
                with client.stream(
                    "POST", 
                    "http://localhost:8000/chat", 
                    json={"query": query, "session_id": "cli_session_01"}, 
                    headers=HEADERS
                ) as response:
                    
                    if response.status_code not in (200, 206):
                        print(f"\n[오류] {response.status_code} - {response.read().decode('utf-8')}")
                        continue
                        
                    # SSE(Server-Sent Events) 라인 단위 파싱
                    for line in response.iter_lines():
                        if line.startswith("data: "):
                            data_str = line[6:]
                            
                            # 스트리밍 종료 신호
                            if data_str == "[DONE]":
                                break
                                
                            try:
                                data = json.loads(data_str)
                                # 1. 출처(Sources) 데이터 처리
                                if data.get("type") == "sources":
                                    print("\n📑 [참조된 검색 문서]")
                                    for doc in data.get("documents", []):
                                        print(f"  - {doc.get('title', '제목없음')}")
                                    print("\n🤖 답변:\n", end="")
                                # 2. 스트리밍 텍스트 처리
                                elif "content" in data:
                                    print(data["content"], end="", flush=True)
                                elif "message" in data:
                                    print(data["message"], end="", flush=True)
                            except json.JSONDecodeError:
                                # JSON 파싱 실패 시 원문 출력
                                print(data_str, end="", flush=True)
                                
            except httpx.ConnectError:
                print("\n[연결 실패] FastAPI 서버(localhost:8000)가 켜져 있는지 확인해 주세요.")
            except Exception as e:
                print(f"\n[오류 발생] {e}")
            
            print("\n" + "-" * 80)

if __name__ == "__main__":
    chat()