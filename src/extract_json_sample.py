"""
모듈명: extract_json_samples.py
설명: data/raw 경로 내의 JSON 파일들을 순회하며 각 파일의 첫 번째 데이터만 추출해 샘플 파일을 생성하는 스크립트
"""

import json
import logging
from pathlib import Path

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def extract_first_items_as_sample() -> None:
    # 경로 설정
    project_root = Path(__file__).resolve().parent.parent
    raw_dir = project_root / "data" / "raw"
    output_path = raw_dir / "sample_output.json"  # 저장할 샘플 파일명

    if not raw_dir.exists() or not raw_dir.is_dir():
        logger.error(f"대상 디렉터리가 존재하지 않거나 디렉터리가 아닙니다: {raw_dir}")
        return

    samples = {}
    encodings = ["utf-8", "cp949", "euc-kr"]

    # os 모듈을 활용하여 하위 폴더를 제외하고 raw_dir 내의 파일들만 순회
    for item_path in raw_dir.iterdir():
        # 파일이면서 .json 확장자인 경우만 처리 (하위 디렉터리 제외)
        if item_path.is_file() and item_path.suffix.lower() == ".json":
            # 결과 저장용 자기 자신 파일은 샘플 추출 대상에서 제외
            if item_path.name == output_path.name:
                continue

            file_data = None
            # 다중 인코딩 시도
            for encoding in encodings:
                try:
                    with open(item_path, "r", encoding=encoding) as f:
                        file_data = json.load(f)
                    break
                except UnicodeDecodeError:
                    continue
                except json.JSONDecodeError as e:
                    logger.error(f"JSON 파싱 실패 [{item_path.name}]: {e}")
                    break

            if file_data is not None:
                # 데이터 구조가 리스트인 경우 첫 번째 요소 추출, 딕셔너리인 경우 그대로 혹은 키 매칭
                if isinstance(file_data, list) and len(file_data) > 0:
                    samples[item_path.name] = file_data[0]
                    logger.info(f"추출 성공: {item_path.name} (리스트의 첫 번째 항목)")
                elif isinstance(file_data, dict) and len(file_data) > 0:
                    # 딕셔너리인 경우 첫 번째 키-값 쌍 또는 전체 딕셔너리의 첫 구조만 추출
                    first_key = next(iter(file_data))
                    samples[item_path.name] = {first_key: file_data[first_key]}
                    logger.info(f"추출 성공: {item_path.name} (딕셔너리의 첫 번째 키 항목)")
                else:
                    logger.warning(f"비어있거나 지원하지 않는 구조입니다: {item_path.name}")

    # 샘플 결과 파일 저장
    if samples:
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(samples, f, ensure_ascii=False, indent=4)
            logger.info(f"모든 샘플이 성공적으로 저장되었습니다: {output_path}")
        except Exception as e:
            logger.error(f"샘플 파일 저장 중 오류 발생: {e}")
    else:
        logger.warning("추출된 샘플 데이터가 없습니다.")


if __name__ == "__main__":
    extract_first_items_as_sample()