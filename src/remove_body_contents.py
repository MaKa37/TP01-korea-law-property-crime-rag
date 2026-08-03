import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "raw" / "body_DB19_prec.json"

# 파일 경로 지정
file_path = DATA_DIR
output_path = file_path  # 원본을 덮어쓰려면 동일하게 설정 (안전하게 백업 후 실행 권장)

# 삭제할 문구 패턴 (공백 차이나 특수문자 변동에 유연하게 대응하도록 정규식 사용)
target_pattern = re.compile(r"일치하는 판례가 없습니다.*판례명을 확인하여 주십시오")

# 파일 읽기 (UTF-8 인코딩 시도, 실패 시 CP949 시도)
try:
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
except UnicodeDecodeError:
    with open(file_path, "r", encoding="cp949") as f:
        data = json.load(f)

# 처리 방식 선택:
# 1. 'remove_key': 해당 문구가 포함된 키-값 쌍(예: "Law": "...") 자체를 삭제 (추천)
# 2. 'remove_item': 해당 문구가 포함된 객체(항목/행) 전체를 삭제
# 3. 'clear_value': 값만 빈 문자열("")로 변경
mode = "remove_key"

def clean_json_data(data, mode="remove_key"):
    if isinstance(data, list):
        cleaned = []
        for item in data:
            if isinstance(item, dict):
                new_item = {}
                skip_item = False
                for k, v in item.items():
                    if isinstance(v, str) and target_pattern.search(v):
                        if mode == "remove_item":
                            skip_item = True
                            break
                        elif mode == "remove_key":
                            continue  # 해당 키-값 쌍 제외 (삭제 효과)
                        elif mode == "clear_value":
                            new_item[k] = ""
                            continue
                    new_item[k] = v
                
                if not skip_item:
                    # 빈 딕셔너리({})가 아닐 때만 리스트에 추가
                    if new_item:
                        cleaned.append(new_item)
            else:
                cleaned.append(item)
        return cleaned
    elif isinstance(data, dict):
        new_dict = {}
        for k, v in data.items():
            if isinstance(v, str) and target_pattern.search(v):
                if mode == "remove_key":
                    continue
                elif mode == "clear_value":
                    new_dict[k] = ""
                    continue
            new_dict[k] = v
        return new_dict
    return data

# 데이터 정제 실행
cleaned_data = clean_json_data(data, mode=mode)

# 결과 저장 (한글 깨짐 방지를 위해 ensure_ascii=False 설정)
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(cleaned_data, f, ensure_ascii=False, indent=4)

print(f"정제가 완료되었습니다. 저장된 파일: {output_path}")