"""
evaluation/patch_bot_session.py
===============================
rag/bot.py가 session_id를 주고받으며, 응답 완료 시 Redis에 대화를 저장하도록 수정합니다.
"""

import re
from pathlib import Path

BOT_PATH = Path("rag") / "bot.py"

def main():
    if not BOT_PATH.exists():
        print(f"❌ {BOT_PATH} 파일을 찾을 수 없습니다.")
        return

    with open(BOT_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. 함수 시그니처에 session_id 추가
    content = re.sub(
        r'def retrieve\(self,\s*user_query\s*:\s*str\)',
        'def retrieve(self, user_query: str, session_id: str = None)',
        content
    )
    content = re.sub(
        r'def ask_stream\(self,\s*user_query\s*:\s*str\)',
        'def ask_stream(self, user_query: str, session_id: str = None)',
        content
    )

    # 2. 내부 함수 호출 시 session_id 전달
    content = content.replace(
        "expand_query(original_query, getattr(self, 'config', None))",
        "expand_query(original_query, getattr(self, 'config', None), session_id)"
    )
    content = content.replace(
        "top_docs = self.retrieve(user_query)",
        "top_docs = self.retrieve(user_query, session_id)"
    )

    # 3. ask_stream 마지막에 Redis 저장 로직 주입
    pattern = re.compile(r"([ \t]*)(latency\s*=\s*time\.time\(\)\s*-\s*start_time\s*\n\s*self\.logger\.info\([^)]*\[stream\] 답변 생성 완료.*?\))", re.DOTALL)
    
    def replacer(match):
        indent = match.group(1)
        original = match.group(2)
        injection = f"""{indent}# [추가] 대화 종료 시 Redis에 기록 저장
{indent}if session_id:
{indent}    try:
{indent}        from db.redis_history import RedisChatHistory
{indent}        history_db = RedisChatHistory()
{indent}        final_answer = ""
{indent}        if 'collected' in locals():
{indent}            final_answer = "".join(collected)
{indent}        elif 'full_answer' in locals():
{indent}            final_answer = full_answer
{indent}        elif 'fallback' in locals():
{indent}            final_answer = fallback
{indent}        if final_answer:
{indent}            history_db.add_message(session_id, "user", original_query if 'original_query' in locals() else user_query)
{indent}            history_db.add_message(session_id, "assistant", final_answer)
{indent}    except Exception as e:
{indent}        self.logger.error(f"Redis 저장 실패: {{e}}")

{indent}{original}"""
        return injection

    new_content, count = pattern.subn(replacer, content)

    if count > 0:
        with open(BOT_PATH, "w", encoding="utf-8") as f:
            f.write(new_content)
        print("✅ rag/bot.py 수정 완료! (Redis 세션 저장 및 전달 로직 추가)")
    else:
        print("⚠️ 패치 대상을 찾지 못했습니다.")

if __name__ == "__main__":
    main()