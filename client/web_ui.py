"""
client/web_ui.py
================
Streamlit 기반 실시간 법률 RAG 채팅 UI (v2 최종 배포본)
- 옵시디언 스타일 계층형 헤더(# ~ ####) 및 굵은 서수(**1)**, - **①**) 완벽 렌더링
- 하위 중첩 리스트 및 날짜(2010. 4. 8.) 공백 정규화
- 판례 원문 전용 옵시디언 콜아웃 카드 뷰
- 실시간 SSE 스트리밍, 답변 재생성(Retry), 100% 클립보드 복사
- 하단 액션 툴바 아이콘 UI 4종 규격 및 스타일 완전 동기화 (36x36px, iframe 아티팩트 제거)
"""

import html
import json
import os
import re
import uuid
from typing import Any, Dict, List, Optional

import httpx
import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

# --- 0. 환경 변수 로드 ---
load_dotenv()
API_KEY: str = os.getenv("API_KEYS", "test-secret-key-1").split(",")[0].strip()
BACKEND_URL: str = os.getenv("BACKEND_URL", "http://localhost:8000/chat")
TIMEOUT_SECONDS: float = 90.0


# --- 1. 페이지 설정 및 디자인 시스템 CSS ---
st.set_page_config(
    page_title="재산범죄 법률 AI 어시스턴트",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    @import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css');

    html, body, [class*="css"], .stMarkdown, .stText {
        font-family: "Pretendard Variable", Pretendard, -apple-system, BlinkMacSystemFont, system-ui, Roboto, "Apple SD Gothic Neo", "Malgun Gothic", sans-serif !important;
        letter-spacing: -0.012em;
    }

    /* 본문 줄간격 및 한국어 단어 줄바꿈 */
    .stMarkdown p, .stMarkdown div {
        line-height: 1.8 !important;
        word-break: keep-all !important;
        overflow-wrap: break-word !important;
        margin-bottom: 0.85rem !important;
    }

    /* 옵시디언 스타일 헤더 계층 구조 */
    .stMarkdown h1 { 
        font-size: 1.55rem !important; 
        font-weight: 800 !important; 
        margin-top: 1.6rem !important; 
        margin-bottom: 0.8rem !important; 
        padding-bottom: 0.4rem !important; 
        border-bottom: 2px solid rgba(128, 128, 128, 0.25) !important;
    }
    .stMarkdown h2 { 
        font-size: 1.32rem !important; 
        font-weight: 700 !important; 
        margin-top: 1.4rem !important; 
        margin-bottom: 0.6rem !important; 
        border-bottom: 1px solid rgba(128, 128, 128, 0.18) !important; 
        padding-bottom: 0.3rem !important; 
    }
    .stMarkdown h3 { 
        font-size: 1.15rem !important; 
        font-weight: 700 !important; 
        margin-top: 1.2rem !important; 
        margin-bottom: 0.5rem !important; 
        color: #4a90e2 !important;
    }
    .stMarkdown h4 { 
        font-size: 1.02rem !important; 
        font-weight: 600 !important; 
        margin-top: 1.0rem !important; 
        margin-bottom: 0.4rem !important; 
    }

    /* 강조 텍스트 시인성 강화 */
    .stMarkdown strong {
        font-weight: 700 !important;
        color: inherit;
    }

    /* 리스트 및 계층형 불릿 여백 */
    .stMarkdown ul, .stMarkdown ol {
        padding-left: 1.3rem !important;
        margin-top: 0.4rem !important;
        margin-bottom: 0.8rem !important;
    }
    .stMarkdown li {
        margin-bottom: 0.35rem !important;
        line-height: 1.75 !important;
    }
    .stMarkdown li > ul, .stMarkdown li > ol {
        margin-top: 0.25rem !important;
        margin-bottom: 0.25rem !important;
        padding-left: 1.2rem !important;
    }

    /* 옵시디언 인용구 / 콜아웃 */
    .stMarkdown blockquote {
        border-left: 4px solid #7057ff !important;
        background-color: rgba(112, 87, 255, 0.05) !important;
        padding: 0.6rem 1rem !important;
        margin: 0.8rem 0 !important;
        border-radius: 0 6px 6px 0 !important;
        font-style: normal !important;
    }

    /* 판례 원문 전용 단일 카드 블록 */
    .legal-callout-card {
        background-color: rgba(128, 128, 128, 0.04);
        border: 1px solid rgba(128, 128, 128, 0.18);
        border-left: 4px solid #4a90e2 !important;
        border-radius: 4px 8px 8px 4px;
        padding: 1.1rem 1.3rem;
        margin: 0.8rem 0 1.0rem 0;
        font-size: 0.91rem;
        line-height: 1.8;
        white-space: pre-wrap !important;
        word-break: keep-all !important;
        overflow-wrap: break-word !important;
        max-height: 460px;
        overflow-y: auto;
    }

    /* ========================================================= */
    /* [액션 툴바 아이콘 완전 동기화 및 iframe 잔상 제거]        */
    /* ========================================================= */

    /* 1. 컬럼 컨테이너 정렬 및 마진 초기화 */
    div[data-testid="column"] {
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
    }

    div[data-testid="column"] > div {
        margin: 0 !important;
        padding: 0 !important;
        width: 36px !important;
        height: 36px !important;
    }

    /* 2. 일반 st.button (👍, 👎, 🔄) 36px 스타일 */
    div[data-testid="column"] div[data-testid="stButton"] > button {
        width: 36px !important;
        height: 36px !important;
        min-width: 36px !important;
        min-height: 36px !important;
        max-width: 36px !important;
        max-height: 36px !important;
        padding: 0 !important;
        margin: 0 !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        border-radius: 8px !important;
        border: 1px solid rgba(128, 128, 128, 0.25) !important;
        background-color: rgba(128, 128, 128, 0.08) !important;
        color: inherit !important;
        font-size: 0.98rem !important;
        line-height: 1 !important;
        outline: none !important;
        box-shadow: none !important;
        transition: all 0.15s ease-in-out !important;
    }

    div[data-testid="column"] div[data-testid="stButton"] > button:hover {
        border-color: rgba(128, 128, 128, 0.55) !important;
        background-color: rgba(128, 128, 128, 0.16) !important;
    }

    div[data-testid="column"] div[data-testid="stButton"] > button:active {
        transform: scale(0.95) !important;
    }

    /* 3. 복사 버튼 iframe (📋) 모서리 아티팩트 및 아웃라인 완전 박멸 */
    div[data-testid="column"] div[data-testid="stIFrame"],
    div[data-testid="column"] div[data-testid="stIFrame"] > iframe,
    div[data-testid="column"] iframe {
        width: 36px !important;
        height: 36px !important;
        min-width: 36px !important;
        min-height: 36px !important;
        max-width: 36px !important;
        max-height: 36px !important;
        display: block !important;
        border: none !important;
        outline: none !important;
        box-shadow: none !important;
        background: transparent !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow: hidden !important;
    }
</style>
""", unsafe_allow_html=True)


# --- 2. 법률 마크다운 렌더링 정규화 헬퍼 ---
def sanitize_legal_markdown(text: str) -> str:
    """
    옵시디언 마크다운 구조(**볼드**, 헤더, 계층 불릿)를 완벽 보존하면서
    Streamlit CommonMark 파서 충돌을 방지합니다.
    """
    if not text:
        return ""

    parts = text.split("```")
    sanitized_parts = []

    for idx, part in enumerate(parts):
        if idx % 2 == 0:
            lines = part.split("\n")
            cleaned_lines = []

            for line in lines:
                line = line.replace("\t", "    ")

                # 헤더 라인 보존
                if re.match(r"^\s*#{1,6}\s+", line):
                    cleaned_lines.append(line.strip())
                    continue

                # 리스트 불릿 들여쓰기 2칸 단위 정규화
                bullet_match = re.match(r"^(\s*)([-*+])\s+(.*)$", line)
                if bullet_match:
                    indent, marker, content = bullet_match.groups()
                    indent_level = len(indent) // 2
                    normalized_indent = "  " * min(indent_level, 3)
                    cleaned_lines.append(f"{normalized_indent}{marker} {content}")
                    continue

                # 문단 시작 4칸 이상 공백의 의도치 않은 코드블록 변환 방지
                line = re.sub(r"^ {4,}", "  ", line)

                # 날짜 및 서수 뒤 다중 공백 정규화
                line = re.sub(r"(?<=\d[.)])\s{2,}", " ", line)

                # 단순 번호가 ol 태그로 변환되는 것 방지
                if re.match(r"^\s*(?:[0-9]+|[가-힣])[.)]\s+", line):
                    line = re.sub(r"^(\s*(?:[0-9]+|[가-힣]))([.)])\s+", r"\1\2" + "\u00A0", line)

                # 단독 물결(~) 문법 오작동 방지
                line = re.sub(r"(?<!\\)~", r"\~", line)
                cleaned_lines.append(line)

            sanitized_parts.append("\n".join(cleaned_lines))
        else:
            sanitized_parts.append(part)

    result = "```".join(sanitized_parts)
    return re.sub(r"\n{3,}", "\n\n", result)


# --- 3. 클립보드 복사 자바스크립트 컴포넌트 ---
def render_copy_button(text: str, key_id: str):
    """36x36px 크기로 완벽하게 규격화된 네이티브 클립보드 복사 버튼"""
    escaped_payload = json.dumps(text)
    html_code = f"""
    <style>
        * {{
            box-sizing: border-box;
            outline: none !important;
        }}
        html, body {{
            margin: 0;
            padding: 0;
            width: 36px;
            height: 36px;
            overflow: hidden;
            background: transparent !important;
        }}
        .custom-icon-btn {{
            display: flex;
            align-items: center;
            justify-content: center;
            width: 36px;
            height: 36px;
            border-radius: 8px;
            background-color: rgba(128, 128, 128, 0.08);
            border: 1px solid rgba(128, 128, 128, 0.25);
            font-size: 0.98rem;
            cursor: pointer;
            padding: 0;
            margin: 0;
            line-height: 1;
            outline: none;
            box-shadow: none;
            transition: all 0.15s ease-in-out;
            user-select: none;
        }}
        .custom-icon-btn:hover {{
            border-color: rgba(128, 128, 128, 0.55);
            background-color: rgba(128, 128, 128, 0.16);
        }}
        .custom-icon-btn:active {{
            transform: scale(0.95);
        }}
    </style>

    <button id="copy-btn-{key_id}" class="custom-icon-btn" onclick="handleCopy()" title="답변 복사">
        <span id="icon-{key_id}">📋</span>
    </button>

    <script>
        function handleCopy() {{
            const rawText = {escaped_payload};
            if (navigator.clipboard && window.isSecureContext) {{
                navigator.clipboard.writeText(rawText)
                    .then(() => showSuccess())
                    .catch(() => fallbackCopy(rawText));
            }} else {{
                fallbackCopy(rawText);
            }}
        }}

        function fallbackCopy(text) {{
            const textArea = document.createElement("textarea");
            textArea.value = text;
            textArea.style.position = "fixed";
            textArea.style.top = "0";
            textArea.style.left = "0";
            textArea.style.opacity = "0";
            document.body.appendChild(textArea);
            textArea.focus();
            textArea.select();
            try {{
                const success = document.execCommand('copy');
                if (success) showSuccess();
            }} catch (err) {{
                console.error("Copy failed: ", err);
            }}
            document.body.removeChild(textArea);
        }}

        function showSuccess() {{
            const icon = document.getElementById("icon-{key_id}");
            const btn = document.getElementById("copy-btn-{key_id}");
            icon.innerText = "✅";
            btn.style.borderColor = "#4CAF50";
            setTimeout(() => {{
                icon.innerText = "📋";
                btn.style.borderColor = "rgba(128, 128, 128, 0.25)";
            }}, 1500);
        }}
    </script>
    """
    components.html(html_code, height=36, width=36)


# --- 4. 세션 상태 관리 ---
if "conversations" not in st.session_state:
    st.session_state.conversations = {}

if "current_session_id" not in st.session_state:
    new_id = str(uuid.uuid4())
    st.session_state.current_session_id = new_id
    st.session_state.conversations[new_id] = {"title": "새로운 법률 상담", "messages": []}

if "retry_query" not in st.session_state:
    st.session_state.retry_query = None


def start_new_chat():
    new_id = str(uuid.uuid4())
    st.session_state.current_session_id = new_id
    st.session_state.conversations[new_id] = {"title": "새로운 법률 상담", "messages": []}


def select_chat(session_id: str):
    st.session_state.current_session_id = session_id


# --- 5. 출처 및 액션 툴바 렌더러 ---
def render_sources(sources_list: List[Dict[str, Any]]):
    """판례별 개별 토글(Expander)로 원문 렌더링"""
    if not sources_list:
        return

    st.markdown("##### 📑 참고 법령 및 판례")

    for idx, doc in enumerate(sources_list):
        title = doc.get("title", "법률 문서")
        court = doc.get("court_name", "")
        date = doc.get("issue_date", "")
        case_no = doc.get("case_number", "")
        full_text = doc.get("full_text", "").strip()

        full_text = re.sub(r"(\d+)\.\s{2,}", r"\1. ", full_text)
        meta_info = f"({court} {date})" if (court or date) else ""
        header_title = f"{idx + 1}. {title} {f'[{case_no}]' if case_no else ''} {meta_info}".strip()
        sanitized_text = html.escape(full_text)

        with st.expander(f"⚖️ {header_title}", expanded=False):
            card_html = f"""
            <div class="legal-callout-card" style="margin-top: 0.2rem; border-left: 3px solid #4a90e2;">
                <div>{sanitized_text}</div>
            </div>
            """
            st.markdown(card_html, unsafe_allow_html=True)


def render_action_bar(content: str, msg_idx: int, user_query_for_retry: str = ""):
    """피드백, 답변 재생성 및 복사 툴바"""
    if not content or content.startswith("⚠️"):
        return

    # 4개 버튼을 좌측에 균등 배치
    col1, col2, col3, col4, _ = st.columns([0.045, 0.045, 0.045, 0.045, 0.82], gap="small")

    with col1:
        if st.button("👍", key=f"btn_like_{msg_idx}", help="도움이 되었어요"):
            st.toast("피드백이 반영되었습니다.", icon="✅")

    with col2:
        if st.button("👎", key=f"btn_dislike_{msg_idx}", help="답변이 아쉬워요"):
            st.toast("피드백을 기록했습니다.", icon="📝")

    with col3:
        if st.button("🔄", key=f"btn_retry_{msg_idx}", help="답변 다시 생성"):
            if user_query_for_retry:
                curr_session = st.session_state.conversations[st.session_state.current_session_id]
                if curr_session["messages"] and curr_session["messages"][-1]["role"] == "assistant":
                    curr_session["messages"].pop()
                st.session_state.retry_query = user_query_for_retry
                st.rerun()

    with col4:
        render_copy_button(content, f"msg_{msg_idx}")


# --- 6. 사이드바 구성 ---
with st.sidebar:
    st.title("⚖️ 법률 상담 내역")
    if st.button("➕ 새 상담 시작", use_container_width=True, type="primary"):
        start_new_chat()
        st.rerun()

    st.markdown("---")
    st.caption("대화 히스토리")

    session_keys = list(st.session_state.conversations.keys())
    for s_id in reversed(session_keys):
        conv = st.session_state.conversations[s_id]
        label = conv["title"]
        is_current = (s_id == st.session_state.current_session_id)

        c1, c2 = st.columns([0.84, 0.16])
        with c1:
            if st.button(f"{'📌 ' if is_current else ''}{label}", key=f"sess_{s_id}", use_container_width=True):
                select_chat(s_id)
                st.rerun()
        with c2:
            if st.button("✕", key=f"del_{s_id}", help="삭제"):
                del st.session_state.conversations[s_id]
                if not st.session_state.conversations:
                    start_new_chat()
                elif st.session_state.current_session_id == s_id:
                    st.session_state.current_session_id = list(st.session_state.conversations.keys())[-1]
                st.rerun()

    st.markdown("---")
    st.caption(f"Session ID: `{st.session_state.current_session_id[:8]}...`")


# --- 7. 메인 화면 및 기존 대화 렌더링 ---
st.title("⚖️ 재산범죄 전문 법률 AI 어시스턴트")
st.caption("사기·횡령·배임 등 재산범죄 전문 판례 및 형법 데이터를 기반으로 실시간 법률 자문을 지원합니다.")

curr_session = st.session_state.conversations[st.session_state.current_session_id]
messages = curr_session["messages"]

for idx, msg in enumerate(messages):
    with st.chat_message(msg["role"]):
        st.markdown(sanitize_legal_markdown(msg["content"]))
        if msg.get("sources"):
            render_sources(msg["sources"])
        if msg["role"] == "assistant" and msg.get("content"):
            prev_user_q = ""
            if idx > 0 and messages[idx - 1]["role"] == "user":
                prev_user_q = messages[idx - 1]["content"]
            render_action_bar(msg["content"], idx, prev_user_q)


# --- 8. 질의 입력 및 백엔드 SSE 스트리밍 ---
input_query = st.chat_input("사건 내용이나 법률 질문을 입력해 주세요.")

active_query: Optional[str] = None
is_regenerating: bool = False

if st.session_state.retry_query:
    active_query = st.session_state.retry_query
    st.session_state.retry_query = None
    is_regenerating = True
elif input_query:
    active_query = input_query

if active_query:
    if len(messages) == 0:
        curr_session["title"] = active_query[:16] + ("..." if len(active_query) > 16 else "")

    if not is_regenerating:
        messages.append({"role": "user", "content": active_query})
        with st.chat_message("user"):
            st.markdown(sanitize_legal_markdown(active_query))

    with st.chat_message("assistant"):
        status_placeholder = st.empty()
        message_placeholder = st.empty()
        sources_placeholder = st.empty()
        action_placeholder = st.empty()

        full_response = ""
        sources_list: List[Dict[str, Any]] = []
        is_first_token = True

        status_msg = "🔄 답변을 재구성하고 있습니다..." if is_regenerating else "🔍 관련 판례 및 형법 법령을 분석 중입니다..."
        with status_placeholder.status(status_msg, expanded=True) as status:
            try:
                headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}
                payload = {
                    "query": active_query,
                    "conversation_id": st.session_state.current_session_id
                }

                with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
                    with client.stream("POST", BACKEND_URL, json=payload, headers=headers) as response:
                        if response.status_code != 200:
                            raise httpx.HTTPStatusError(
                                f"HTTP {response.status_code}",
                                request=response.request,
                                response=response
                            )

                        for line in response.iter_lines():
                            if line.startswith("data: "):
                                data_str = line[6:].strip()
                                if data_str == "[DONE]":
                                    break

                                try:
                                    data = json.loads(data_str)

                                    if data.get("type") == "sources":
                                        sources_list = data.get("documents", [])
                                        status.update(label="📑 판례 검토 완료. 법률 분석 답변을 작성합니다...", state="running")

                                    elif "content" in data:
                                        if is_first_token:
                                            status_placeholder.empty()
                                            is_first_token = False

                                        full_response += data["content"]
                                        message_placeholder.markdown(sanitize_legal_markdown(full_response) + " ▌")

                                except json.JSONDecodeError:
                                    continue

            except httpx.ConnectError:
                status_placeholder.empty()
                full_response = "⚠️ **백엔드 서버에 연결할 수 없습니다.** 서버가 실행 중인지 확인해 주세요."
            except Exception as e:
                status_placeholder.empty()
                full_response = f"⚠️ **통신 오류 발생:** {str(e)}"

        # 스트리밍 완료 후 커서 제거 및 최종 렌더링
        status_placeholder.empty()
        message_placeholder.markdown(sanitize_legal_markdown(full_response))

        if sources_list:
            with sources_placeholder.container():
                render_sources(sources_list)

        if full_response and not full_response.startswith("⚠️"):
            with action_placeholder.container():
                render_action_bar(full_response, len(messages), active_query)

        messages.append({
            "role": "assistant",
            "content": full_response,
            "sources": sources_list
        })