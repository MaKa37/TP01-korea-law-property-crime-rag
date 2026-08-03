"""
모듈명: body_prec_cleaner.py
설명: 법원 판례 데이터셋 중 조회 불가/일치하지 않는 판례 데이터를 정제(삭제)하는 스크립트
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Union

# 로깅 설정 (현업에서는 print 대신 logging 모듈을 사용하여 로그 레벨 및 타임스탬프 관리)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# 정규식 패턴 사전 컴파일 (성능 최적화)
TARGET_PATTERN = re.compile(r"일치하는 판례가 없습니다.*판례명을 확인하여 주십시오")


def clean_json_data(data: Any, target_pattern: re.compile, mode: str = "remove_key") -> Any:
    """JSON 데이터 구조 내에서 특정 문구가 포함된 키 또는 항목을 재귀적으로 정제합니다.

    Args:
        data (Any): 정제할 JSON 데이터 (List 또는 Dict)
        target_pattern (re.compile): 검색할 대상 정규식 패턴
        mode (str): 처리 방식
            - 'remove_key': 해당 문구가 포함된 키-값 쌍 삭제 (기본값)
            - 'remove_item': 해당 문구가 포함된 객체(항목/행) 전체 삭제
            - 'clear_value': 값만 빈 문자열("")로 변경

    Returns:
        Any: 정제된 데이터 구조
    """
    if isinstance(data, list):
        cleaned_list = []
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
                            continue  # 해당 키-값 쌍 제외
                        elif mode == "clear_value":
                            new_item[k] = ""
                            continue
                    new_item[k] = v
                
                # 항목 전체 삭제가 아니고, 키가 모두 제거되어 빈 딕셔너리({})가 된 경우가 아닐 때만 추가
                if not skip_item and new_item:
                    cleaned_list.append(new_item)
            else:
                cleaned_list.append(item)
        return cleaned_list

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


def main() -> None:
    # 경로 설정
    project_root = Path(__file__).resolve().parent.parent
    file_path = project_root / "data" / "raw" / "body_DB19_prec.json"
    output_path = file_path  # 원본 덮어쓰기

    if not file_path.exists():
        logger.error(f"대상 파일이 존재하지 않습니다: {file_path}")
        return

    # 다중 인코딩 지원을 통한 파일 로드 안정성 확보
    encodings = ["utf-8", "cp949", "euc-kr"]
    data = None
    
    for encoding in encodings:
        try:
            with open(file_path, "r", encoding=encoding) as f:
                data = json.load(f)
            logger.info(f"파일 로드 성공 [인코딩: {encoding}] - 경로: {file_path}")
            break
        except UnicodeDecodeError:
            continue
        except json.JSONDecodeError as e:
            logger.error(f"JSON 파일 파싱 실패 (문법 오류): {e}")
            return

    if data is None:
        logger.error("지원하는 모든 인코딩(UTF-8, CP949 등)으로도 파일을 읽을 수 없습니다.")
        return

    # 데이터 정제 실행 ('remove_key' 모드 적용)
    mode = "remove_key"
    logger.info(f"데이터 정제 프로세스 시작 (Mode: {mode})")
    cleaned_data = clean_json_data(data, TARGET_PATTERN, mode=mode)

    # 결과 저장 (디렉터리 자동 생성 및 원자적 쓰기 고려)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(cleaned_data, f, ensure_ascii=False, indent=4)
        logger.info(f"정제가 완료되어 파일이 성공적으로 저장되었습니다: {output_path}")
    except Exception as e:
        logger.error(f"파일 저장 중 오류가 발생했습니다: {e}")


if __name__ == "__main__":
    main()