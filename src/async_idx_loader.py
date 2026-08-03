"""
법제처 국가법령정보 Open API 비동기 전체 데이터 수집 및 파일 적재 파이프라인.

법제처 Open API의 페이지네이션(Pagination) 기능을 활용하여 대상(Target)의 
'모든 데이터'를 비동기로 수집하고 하나의 파일로 병합하여 적재합니다.

[주요 기능 및 특징]
    - Pagination 처리: page=1 호출 후 totalCnt를 기반으로 전체 페이지 계산 및 병렬 호출
    - Semaphore 최적화: Target 단위가 아닌 Request 단위로 동시성을 제어하여 I/O 극대화
    - Auto Retry (지수 백오프): 네트워크 일시 단절 및 5xx 에러에 대한 자동 재시도 로직
    - 메모리 통합 적재: 수집된 모든 페이지의 데이터를 병합하여 단일 JSON/XML 파일로 저장

[실행 환경]
    - Python >= 3.11
    - Dependencies: aiohttp, aiofiles, python-dotenv
"""

import asyncio
import json
import logging
import math
import os
import xml.etree.ElementTree as ET
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Coroutine, TypeAlias, TypedDict

import aiofiles
import aiohttp
from aiohttp import ClientTimeout
from dotenv import load_dotenv

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s",
)
logger = logging.getLogger(__name__)

# ==============================================================================
# 1. 설정 및 타입 정의 (Configuration & Types)
# ==============================================================================

BASE_URL = "https://www.law.go.kr/DRF/lawSearch.do"
REQUEST_TIMEOUT_SECONDS = 30
MAX_CONCURRENT_REQUESTS = 20  # 동시 요청 수 제한
MAX_RETRIES = 1               # API 호출 실패 시 최대 재시도 횟수
DISPLAY_COUNT = 100           # 페이지당 요청 건수 (법제처 API 권장 최대치)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "raw"

LawItem: TypeAlias = dict[str, Any]


class ResponseFormat(StrEnum):
    JSON = "JSON"
    XML = "XML"


class TargetSpec(TypedDict):
    db_name: str
    target: str
    description: str
    format: ResponseFormat


class LawApiError(Exception):
    pass


# ==============================================================================
# 2. 비동기 데이터 수집 (Async Fetching with Pagination & Retry)
# ==============================================================================

async def _fetch_payload_with_retry(
    session: aiohttp.ClientSession,
    api_key: str,
    target_name: str,
    response_format: ResponseFormat,
    page: int,
    semaphore: asyncio.Semaphore,
) -> str:
    """API 요청을 수행하며, 실패 시 지수 백오프(Exponential Backoff)로 재시도합니다."""
    params = {
        "OC": api_key,
        "target": target_name,
        "type": response_format,
        "display": str(DISPLAY_COUNT),
        "page": str(page),
    }

    # 커넥션 풀 및 서버 부하 보호를 위해 실제 요청 직전에 Semaphore 획득
    async with semaphore:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with session.get(BASE_URL, params=params) as response:
                    response.raise_for_status()
                    return await response.text()

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt == MAX_RETRIES:
                    raise LawApiError(f"요청 실패 (target={target_name}, page={page}): {e}") from e
                
                wait_time = 2 ** attempt  # 2초, 4초, 8초 대기
                logger.warning(f"요청 재시도 ({attempt}/{MAX_RETRIES}) - {target_name} [page:{page}] - {wait_time}초 대기")
                await asyncio.sleep(wait_time)


async def fetch_json_page(
    session: aiohttp.ClientSession, api_key: str, target_name: str, page: int, semaphore: asyncio.Semaphore
) -> tuple[list[LawItem], int]:
    """JSON 데이터를 조회하고 (데이터목록, 전체건수) 튜플을 반환합니다."""
    content = await _fetch_payload_with_retry(session, api_key, target_name, ResponseFormat.JSON, page, semaphore)

    try:
        payload = json.loads(content)
    except json.JSONDecodeError as e:
        raise LawApiError(f"JSON 파싱 실패 (target={target_name}, page={page}): {e}") from e

    if not payload:
        return [], 0

    root_key = next(iter(payload), None)
    if root_key is None:
        return [], 0

    target_data = payload[root_key]
    
    # 전체 건수 추출
    total_cnt = int(target_data.get("totalCnt", 0)) if isinstance(target_data, dict) else 0
    
    # 데이터 목록 추출
    results = target_data.get(target_name, []) if isinstance(target_data, dict) else []
    
    # 응답이 1건일 경우 dict로 오는 이슈 방어 (list로 통일)
    if isinstance(results, dict):
        results = [results]

    return results, total_cnt


async def fetch_xml_page(
    session: aiohttp.ClientSession, api_key: str, target_name: str, page: int, semaphore: asyncio.Semaphore
) -> tuple[list[LawItem], int]:
    """XML 데이터를 조회하고 (데이터목록, 전체건수) 튜플을 반환합니다."""
    content = await _fetch_payload_with_retry(session, api_key, target_name, ResponseFormat.XML, page, semaphore)

    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        raise LawApiError(f"XML 파싱 실패 (target={target_name}, page={page}): {e}") from e

    # 전체 건수 추출
    total_cnt_elem = root.find(".//totalCnt")
    total_cnt = int(total_cnt_elem.text) if total_cnt_elem is not None and total_cnt_elem.text else 0

    # 데이터 목록 추출
    items = []
    for node in root.findall(f".//{target_name}"):
        item_dict = {child.tag: (child.text or "").strip() for child in node}
        items.append(item_dict)

    return items, total_cnt


# ==============================================================================
# 3. 비동기 전체 데이터 병합 및 파일 적재 (Bulk Extract & Load)
# ==============================================================================

async def collect_all_data(
    session: aiohttp.ClientSession, 
    api_key: str, 
    spec: TargetSpec, 
    semaphore: asyncio.Semaphore
) -> list[LawItem]:
    """1페이지를 호출하여 총 건수를 파악한 뒤, 나머지 모든 페이지를 병렬로 호출하여 취합합니다."""
    
    # 포맷에 따른 파서 라우팅
    fetcher: Callable = fetch_json_page if spec["format"] == ResponseFormat.JSON else fetch_xml_page
    
    logger.info(f"[{spec['db_name']}] 메타데이터 수집 중 (Page 1)...")
    
    # 1. 첫 페이지 호출 (총 데이터 건수 파악)
    first_page_items, total_cnt = await fetcher(session, api_key, spec["target"], 1, semaphore)
    
    if total_cnt == 0 or not first_page_items:
        logger.warning(f"[{spec['db_name']}] 수집할 데이터가 없습니다.")
        return []

    total_pages = math.ceil(total_cnt / DISPLAY_COUNT)
    logger.info(f"[{spec['db_name']}] 총 {total_cnt}건 (총 {total_pages}페이지) 수집을 시작합니다.")

    all_items = list(first_page_items)

    # 2. 2페이지부터 마지막 페이지까지 비동기 태스크 생성
    if total_pages > 1:
        tasks = [
            fetcher(session, api_key, spec["target"], page, semaphore)
            for page in range(2, total_pages + 1)
        ]
        
        # 병렬 호출 실행 및 결과 취합
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for idx, result in enumerate(results):
            page_num = idx + 2
            if isinstance(result, Exception):
                logger.error(f"[{spec['db_name']}] Page {page_num} 수집 실패 누락: {result}")
            else:
                items, _ = result
                all_items.extend(items)

    logger.info(f"[{spec['db_name']}] 총 {len(all_items)}건 수집 완료.")
    return all_items


def _ensure_partition_dir() -> Path:
    partition_dir = DATA_DIR
    partition_dir.mkdir(parents=True, exist_ok=True)
    return partition_dir


async def save_to_file(items: list[LawItem], spec: TargetSpec) -> Path:
    """수집된 모든 데이터를 지정된 포맷으로 변환하여 로컬 파일에 기록합니다."""
    partition_dir = _ensure_partition_dir()
    file_name = f"idx_{spec['db_name']}_{spec['target']}.{spec['format'].lower()}"
    file_path = partition_dir / file_name

    if not items:
        # 빈 데이터도 추적을 위해 빈 파일로 남김
        async with aiofiles.open(file_path, mode="w", encoding="utf-8") as f:
            await f.write("[]" if spec["format"] == ResponseFormat.JSON else f"<{spec['target']}List/>")
        return file_path

    if spec["format"] == ResponseFormat.JSON:
        async with aiofiles.open(file_path, mode="w", encoding="utf-8") as f:
            # 메모리 최적화를 위해 indent 없이 저장할 수도 있으나 가독성을 위해 포맷팅 적용
            await f.write(json.dumps(items, ensure_ascii=False, indent=2))

    elif spec["format"] == ResponseFormat.XML:
        # XML 루트는 리스트를 의미하는 태그로 감쌈 (예: <lawList>)
        root = ET.Element(f"{spec['target']}List")
        for item_dict in items:
            child = ET.SubElement(root, spec['target'])
            for key, value in item_dict.items():
                sub_elem = ET.SubElement(child, key)
                sub_elem.text = str(value)
        
        ET.indent(root, space="  ", level=0)
        xml_str = ET.tostring(root, encoding="utf-8").decode("utf-8")
        
        async with aiofiles.open(file_path, mode="w", encoding="utf-8") as f:
            await f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            await f.write(xml_str)

    return file_path


# ==============================================================================
# 4. 파이프라인 오케스트레이션 (Orchestration)
# ==============================================================================

async def process_target(
    session: aiohttp.ClientSession, 
    api_key: str, 
    spec: TargetSpec, 
    semaphore: asyncio.Semaphore
) -> None:
    """단일 Target에 대한 [전체 페이지 수집 -> 병합 -> 파일 적재] 과정을 통제합니다."""
    try:
        # 1. 전체 데이터 수집
        all_data = await collect_all_data(session, api_key, spec, semaphore)
        
        # 2. 하나의 파일로 적재
        saved_path = await save_to_file(all_data, spec)
        logger.info(f"[{spec['db_name']}] 파일 적재 완료: {saved_path}")
            
    except Exception as e:
        logger.exception(f"[{spec['db_name']}] 파이프라인 처리 중 치명적 오류 발생: {e}")


async def run_pipeline() -> None:
    load_dotenv()
    api_key = os.getenv("LAW_OPEN_API_KEY")

    if not api_key:
        logger.error("환경변수 'LAW_OPEN_API_KEY'가 설정되어 있지 않습니다.")
        raise SystemExit(1)

    target_dbs: list[TargetSpec] = [
        {"db_name": "DB01", "target": "law", "description": "법령", "format": ResponseFormat.JSON},
        {"db_name": "DB03", "target": "expc", "description": "법령해석례", "format": ResponseFormat.JSON},
        {"db_name": "DB10", "target": "lstrm", "description": "법령용어", "format": ResponseFormat.JSON},
        {"db_name": "DB19", "target": "prec", "description": "법원 판례", "format": ResponseFormat.JSON},
    ]

    timeout = ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    # Semaphore는 이제 process_target이 아닌 _fetch_payload_with_retry 단위로 작동합니다.
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        # 각 Target별 수집 파이프라인을 병렬로 구동
        tasks = [
            process_target(session, api_key, spec, semaphore)
            for spec in target_dbs
        ]
        await asyncio.gather(*tasks)

    logger.info("모든 데이터 적재 파이프라인 작업이 종료되었습니다.")


if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    asyncio.run(run_pipeline())