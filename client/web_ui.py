"""
client/web_ui.py
================
Streamlit을 활용한 웹 기반 실시간 채팅 UI입니다.
FastAPI 백엔드의 SSE 스트리밍을 받아 제미나이처럼 글자가 타이핑되는 효과를 줍니다.
"""

import streamlit as st
import uuid
import httpx
import json
import os
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("API_KEYS", "test-secret-key-1").split(",")[0]

# --- 1. 페이지 및 세션 초기화 ---
st.set_page_config(page_title="재산범죄 법률 AI", page_icon="⚖️", layout="centered")
st.title("⚖️ 재산범죄 법률 RAG 어시스턴트")

# 브라우저 탭별로 고유한 세션 ID 부여 (Redis 저장을 위함)
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

# 화면에 표시할 대화 내역 초기화
if "messages" not in st.session_state:
    st.session_state.messages = []

# --- 2. 기존 대화 내용 화면에 렌더링 ---
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        # 참고 문서가 있다면 토글(expander)로 표시
        if "sources" in msg and msg["sources"]:
            with st.expander("📑 참고 판례 및 법령"):
                for doc in msg["sources"]:
                    st.write(f"- {doc.get('title', '제목없음')}")

# --- 3. 사용자 입력 및 스트리밍 통신 ---
user_query = st.chat_input("궁금한 법률 문제나 판례를 질문해 주세요.")

if user_query:
    # 사용자 메시지 화면에 출력 및 저장
    st.session_state.messages.append({"role": "user", "content": user_query})
    with st.chat_message("user"):
        st.markdown(user_query)

    # 봇 응답 스트리밍 처리
    with st.chat_message("assistant"):
        message_placeholder = st.empty()
        sources_placeholder = st.empty()
        
        full_response = ""
        sources_list = []

        try:
            # FastAPI 스트리밍 엔드포인트 호출
            with httpx.Client(timeout=120.0) as client:
                headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}
                payload = {"query": user_query, "conversation_id": st.session_state.session_id}
                
                with client.stream("POST", "http://localhost:8000/chat", json=payload, headers=headers) as response:
                    for line in response.iter_lines():
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str == "[DONE]":
                                break
                            
                            try:
                                data = json.loads(data_str)
                                # 1. 출처(Sources) 정보 렌더링
                                if data.get("type") == "sources":
                                    sources_list = data.get("documents", [])
                                    with sources_placeholder.expander("📑 참고 판례 및 법령"):
                                        for doc in sources_list:
                                            st.write(f"- {doc.get('title', '제목없음')}")
                                
                                # 2. 스트리밍 텍스트 렌더링
                                elif "content" in data:
                                    full_response += data["content"]
                                    message_placeholder.markdown(full_response + "▌") # 커서 깜빡임 효과
                            except json.JSONDecodeError:
                                pass
                                
        except Exception as e:
            full_response = f"⚠️ 서버 연결 오류: {e}"

        # 스트리밍 완료 후 커서(▌) 제거 및 최종 화면 업데이트
        message_placeholder.markdown(full_response)
        
        # 봇의 응답을 세션 상태에 저장 (다음 화면 렌더링을 위해)
        st.session_state.messages.append({
            "role": "assistant", 
            "content": full_response,
            "sources": sources_list
        })