"""
evaluation/patch_bot.py
=======================
rag/bot.py 내부의 grounding 변수 타입 에러(튜플 접근 오류)를 안전하게 수정합니다.
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

    # 정규식을 이용해 들여쓰기를 유지하며 해당 조건문을 안전한 형태로 교체
    def replacer(match):
        indent = match.group(1)
        return (
            f"{indent}# [FIX] 환각 검증(Grounding) 반환값이 튜플로 변경된 것에 대응\n"
            f"{indent}if isinstance(grounding, tuple):\n"
            f"{indent}    grounding = grounding[1] if len(grounding) > 1 and isinstance(grounding[1], dict) else grounding[0]\n"
            f"{indent}if isinstance(grounding, dict) and grounding.get('has_unverified'):"
        )

    # 싱글 쿼트(')와 더블 쿼트(") 모두 대응
    pattern = re.compile(r"([ \t]+)if grounding\[['\"]has_unverified['\"]\]:")
    new_content, count = pattern.subn(replacer, content)

    if count > 0:
        with open(BOT_PATH, "w", encoding="utf-8") as f:
            f.write(new_content)
        print(f"✅ rag/bot.py 수정 완료! ({count}곳 패치 적용됨)")
    else:
        print("⚠️ 패치 대상을 찾지 못했습니다. 이미 수정되었거나 변수명이 다를 수 있습니다.")

if __name__ == "__main__":
    main()