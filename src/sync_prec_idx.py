"""
모듈명: sync_prec_index.py
설명: body_DB19_prec.json의 본문 데이터를 기준으로, 
      idx_DB19_prec.json에만 존재하고 body에 없는(본문이 누락된) 인덱스 데이터를 idx_DB19에서 제거하는 스크립트.
"""

import json
import logging
from pathlib import Path
from typing import Any, Set, Union

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def load_json(file_path: Path) -> Any:
    """다중 인코딩(UTF-8, CP949 등)을 지원하는 안전한 JSON 로더"""
    encodings = ["utf-8", "cp949", "euc-kr"]
    for encoding in encodings:
        try:
            with open(file_path, "r", encoding=encoding) as f:
                data = json.load(f)
            logger.info(f"파일 로드 성공 [인코딩: {encoding}] - 파일명: {file_path.name}")
            return data
        except UnicodeDecodeError:
            continue
        except json.JSONDecodeError as e:
            logger.error(f"JSON 파일 파싱 실패 [{file_path.name}]: {e}")
            raise
    raise UnicodeDecodeError(f"지원하는 인코딩으로 파일을 읽을 수 없습니다: {file_path}")


def extract_ids_from_body(data: Any) -> Set[str]:
    """body_DB19 파일 구조를 탐색하여 모든 유효한 '판례정보일련번호' 집합을 수집합니다."""
    prec_ids = set()
    
    # 1. 리스트 구조인 경우
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                # PrecService 내부 혹은 최상위 키 탐색
                prec_service = item.get("PrecService", item)
                if isinstance(prec_service, dict):
                    prec_id = prec_service.get("판례정보일련번호") or prec_service.get("판례일련번호")
                    if prec_id:
                        prec_ids.add(str(prec_id))
                        
    # 2. 딕셔너리 구조인 경우 (키-값 형태)
    elif isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict):
                prec_service = v.get("PrecService", v)
                if isinstance(prec_service, dict):
                    prec_id = prec_service.get("판례정보일련번호") or prec_service.get("판례일련번호")
                    if prec_id:
                        prec_ids.add(str(prec_id))
            elif isinstance(v, str) and k in ("판례정보일련번호", "판례일련번호"):
                prec_ids.add(str(v))
                
    return prec_ids


def clean_idx_data(idx_data: Any, valid_ids: Set[str]) -> Any:
    """idx_data 중 valid_ids(본문이 존재하는 ID 목록)에 포함되지 않는 항목을 제거합니다."""
    if isinstance(idx_data, list):
        cleaned_list = []
        for item in idx_data:
            if isinstance(item, dict):
                # 인덱스 파일 내의 일련번호 필드명 확인 (판례일련번호, 판례정보일련번호, id 등)
                idx_id = (
                    item.get("판례일련번호") 
                    or item.get("판례정보일련번호") 
                    or item.get("id")
                )
                
                # 본문(body)에 일련번호가 존재할 때만 유지
                if idx_id and str(idx_id) in valid_ids:
                    cleaned_list.append(item)
                else:
                    logger.debug(f"제거된 고아 인덱스 ID: {idx_id}")
            else:
                cleaned_list.append(item)
        return cleaned_list

    elif isinstance(idx_data, dict):
        cleaned_dict = {}
        for k, v in idx_data.items():
            if isinstance(v, dict):
                idx_id = (
                    v.get("판례일련번호") 
                    or v.get("판례정보일련번호") 
                    or v.get("id")
                )
                if (idx_id and str(idx_id) in valid_ids) or (k in valid_ids):
                    cleaned_dict[k] = v
                else:
                    logger.debug(f"제거된 고아 인덱스 키/ID: {k} (idx_id: {idx_id})")
            else:
                cleaned_dict[k] = v
        return cleaned_dict

    return idx_data


def main() -> None:
    # 경로 설정
    project_root = Path(__file__).resolve().parent.parent
    body_path = project_root / "data" / "raw" / "body_DB19_prec.json"
    idx_path = project_root / "data" / "raw" / "idx_DB19_prec.json"

    if not body_path.exists():
        logger.error(f"본문 파일이 존재하지 않습니다: {body_path}")
        return
    if not idx_path.exists():
        logger.error(f"인덱스 파일이 존재하지 않습니다: {idx_path}")
        return

    # 1. 파일 로드
    logger.info("--- 데이터 로드 시작 ---")
    body_data = load_json(body_path)
    idx_data = load_json(idx_path)

    # 2. body_DB19에서 판례 일련번호 수집
    valid_ids = extract_ids_from_body(body_data)
    logger.info(f"body_DB19에서 수집된 유효 판례 ID 총 {len(valid_ids)}개")

    # 3. idx_DB19 정제 (body에 없는 고아 인덱스 제거)
    initial_count = len(idx_data) if isinstance(idx_data, (list, dict)) else 0
    logger.info("--- idx_DB19 정제 프로세스 시작 ---")
    cleaned_idx_data = clean_idx_data(idx_data, valid_ids)
    final_count = len(cleaned_idx_data) if isinstance(cleaned_idx_data, (list, dict)) else 0

    removed_count = initial_count - final_count
    logger.info(f"인덱스 정제 완료: 기존 {initial_count}개 -> 정제 후 {final_count}개 (제거된 고아 인덱스: {removed_count}개)")

    # 4. idx_DB19_prec.json 덮어쓰기 저장
    try:
        with open(idx_path, "w", encoding="utf-8") as f:
            json.dump(cleaned_idx_data, f, ensure_ascii=False, indent=4)
        logger.info(f"idx_DB19_prec.json 파일이 성공적으로 갱신되었습니다: {idx_path}")
    except Exception as e:
        logger.error(f"idx 파일 저장 중 오류 발생: {e}")


if __name__ == "__main__":
    main()