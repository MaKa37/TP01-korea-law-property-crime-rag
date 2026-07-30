"""
DB01, DB03, DB19의 parsed / refined json 파일들에서
각각 첫 번째 딕셔너리(레코드)만 추출해서 별도 파일로 저장하는 스크립트.

전제:
- 각 json 파일은 최상위가 리스트([...]) 형태이고, 그 안에 딕셔너리들이 들어있음.
  (업로드된 이미지 기준: DB01_law_parsed.json 등)

사용법:
    python extract_first_item.py

입력 파일 경로와 출력 폴더는 아래 CONFIG 부분에서 수정 가능.
"""

import json
import os

# ------------------------------
# CONFIG: 필요에 맞게 경로 수정
# ------------------------------
INPUT_DIR = "Json_Files"          # 원본 json들이 있는 폴더
OUTPUT_DIR = "Json_Files/sample"  # 추출 결과를 저장할 폴더

TARGET_FILES = [
    "DB01_law_parsed.json",
    "DB01_law_refined.json",
    "DB03_expc_parsed.json",
    "DB03_expc_refined.json",
    "DB19_prec_parsed.json",
    "DB19_prec_refined.json",
]


def extract_first_item(input_path: str, output_path: str) -> None:
    """input_path의 json(list)에서 첫 번째 원소만 뽑아 output_path에 저장."""
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError(f"{input_path}: 최상위 구조가 list가 아닙니다. (type={type(data)})")

    if len(data) == 0:
        raise ValueError(f"{input_path}: 데이터가 비어있습니다.")

    first_item = data[0]

    with open(output_path, "w", encoding="utf-8") as f:
        # 리스트로 감싸서 저장 (원본과 구조 통일). 딕셔너리 단독으로 저장하고 싶으면
        # json.dump(first_item, f, ...) 로 변경.
        json.dump([first_item], f, ensure_ascii=False, indent=2)

    print(f"[완료] {input_path} -> {output_path}")


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    for filename in TARGET_FILES:
        input_path = os.path.join(INPUT_DIR, filename)
        name, ext = os.path.splitext(filename)
        output_path = os.path.join(OUTPUT_DIR, f"{name}_first{ext}")

        if not os.path.exists(input_path):
            print(f"[건너뜀] 파일이 존재하지 않음: {input_path}")
            continue

        try:
            extract_first_item(input_path, output_path)
        except Exception as e:
            print(f"[에러] {input_path}: {e}")


if __name__ == "__main__":
    main()