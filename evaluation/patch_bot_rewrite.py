"""
evaluation/patch_bot_rewrite.py
===============================
rag/bot.py의 retrieve 함수 내부에 질의 재작성(Query Rewriter) 로직을 안전하게 주입합니다.
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

    if "from rag.query_rewriter import expand_query" in content:
        print("✅ 이미 질의 재작성(Query Rewriting) 모듈이 bot.py에 적용되어 있습니다.")
        return

    # retrieve 함수 내의 첫 줄인 get_embedding 호출을 찾아 그 위에 로직을 주입합니다.
    def replacer(match):
        indent = match.group(1)
        original_line = match.group(2)
        
        injection = f"""{indent}# [추가] 질의 재작성(Query Rewriting) 파이프라인
{indent}try:
{indent}    from rag.query_rewriter import expand_query
{indent}    original_query = user_query
{indent}    user_query = expand_query(original_query, getattr(self, 'config', None))
{indent}    self.logger.info(f"🔄 [Rewriter] '{{original_query}}' -> '{{user_query}}'")
{indent}except Exception as e:
{indent}    self.logger.error(f"Query Rewriter 주입 실패: {{e}}")

{indent}{original_line}"""
        return injection

    # query_vector = get_embedding(...) 구문을 정확히 타겟팅
    pattern = re.compile(r"^([ \t]*)(query_vector\s*=\s*get_embedding\([^)]+\))", re.MULTILINE)
    new_content, count = pattern.subn(replacer, content)

    if count > 0:
        with open(BOT_PATH, "w", encoding="utf-8") as f:
            f.write(new_content)
        print(f"✅ rag/bot.py에 질의 재작성(Query Rewriting) 기능이 성공적으로 주입되었습니다! (적용: {count}곳)")
    else:
        print("⚠️ 패치 대상을 찾지 못했습니다. 코드가 이미 변경되었는지 확인해 주세요.")

if __name__ == "__main__":
    main()