"""
법제처 국가법령정보 Open API 비동기 본문(Detail) 데이터 수집 및 파일 적재 파이프라인.

기존에 수집된 목록(Index) 데이터를 기반으로 고유 식별자를 추출한 뒤,
본문 API(lawService.do)를 비동기로 호출하여 전체 데이터를 하나의 파일로 병합합니다.

[주요 기능 및 특징]
    - 선행 데이터 의존: 이전 파이프라인에서 생성된 idx_*.json/xml 파일을 읽어 ID 추출
    - 동적 파라미터 매핑: 법령(MST), 그 외(ID) 등 대상별로 다른 식별자 파라미터 자동 매핑
    - Semaphore 최적화: 수천~수만 건의 본문 호출 시 서버 부하를 막기 위한 엄격한 동시성 제어
    - Auto Retry (지수 백오프): 본문 텍스트가 커서 발생할 수 있는 Timeout 및 I/O 에러 방어
"""

import asyncio
import json
import logging
import os
import xml.etree.ElementTree as ET
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, TypeAlias, TypedDict

import aiofiles
import aiohttp
from aiohttp import ClientTimeout
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s",
)
logger = logging.getLogger(__name__)

# ==============================================================================
# 1. 설정 및 타입 정의
# ==============================================================================

# 본문 API는 lawSearch.do 가 아닌 lawService.do 를 사용합니다.
BASE_URL = "https://www.law.go.kr/DRF/lawService.do"
REQUEST_TIMEOUT_SECONDS = 60  # 본문 데이터는 크기가 크므로 타임아웃을 넉넉히 줍니다.
MAX_CONCURRENT_REQUESTS = 15  # 본문 호출은 서버 부하가 커서 동시 요청 수를 약간 낮추는 것을 권장합니다.
MAX_RETRIES = 1

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "raw"

LawItem: TypeAlias = dict[str, Any]

class ResponseFormat(StrEnum):
    JSON = "JSON"
    XML = "XML"

class DetailTargetSpec(TypedDict):
    db_name: str
    target: str
    description: str
    format: ResponseFormat
    id_field: str      # 목록 데이터에서 식별자를 추출할 키 (예: '법령일련번호')
    param_key: str     # 본문 API 호출 시 사용할 파라미터 키 (법령은 'MST', 그 외는 보통 'ID')

class LawApiError(Exception):
    pass


# ==============================================================================
# 2. 로컬 목록(Index) 데이터에서 ID 추출
# ==============================================================================

async def extract_ids_from_index(spec: DetailTargetSpec) -> list[str]:
    """이전에 저장된 idx_*.json/xml 파일에서 본문 호출에 필요한 ID를 추출합니다."""
    file_name = f"idx_{spec['db_name']}_{spec['target']}.{spec['format'].lower()}"
    file_path = DATA_DIR / file_name

    if not file_path.exists():
        logger.warning(f"[{spec['db_name']}] 목록 파일이 존재하지 않습니다: {file_path}")
        return []

    ids = []
    async with aiofiles.open(file_path, mode="r", encoding="utf-8") as f:
        content = await f.read()

    if spec["format"] == ResponseFormat.JSON:
        try:
            data = json.loads(content)
            for item in data:
                if spec["id_field"] in item:
                    ids.append(str(item[spec["id_field"]]))
        except json.JSONDecodeError as e:
            logger.error(f"[{spec['db_name']}] 인덱스 JSON 파싱 실패: {e}")

    elif spec["format"] == ResponseFormat.XML:
        try:
            root = ET.fromstring(content)
            for node in root.findall(f".//{spec['target']}"):
                id_node = node.find(spec["id_field"])
                if id_node is not None and id_node.text:
                    ids.append(id_node.text.strip())
        except ET.ParseError as e:
            logger.error(f"[{spec['db_name']}] 인덱스 XML 파싱 실패: {e}")

    # 중복 제거 후 반환 (데이터 무결성 확보)
    unique_ids = list(dict.fromkeys(ids))
    logger.info(f"[{spec['db_name']}] 목록 파일에서 총 {len(unique_ids)}개의 식별자 추출 완료.")
    return unique_ids


# ==============================================================================
# 3. 비동기 본문 데이터 수집 (Async Detail Fetching)
# ==============================================================================

async def fetch_detail_item(
    session: aiohttp.ClientSession,
    api_key: str,
    spec: DetailTargetSpec,
    item_id: str,
    semaphore: asyncio.Semaphore,
) -> Any | None:
    """단일 식별자에 대한 본문 데이터를 조회합니다."""
    params = {
        "OC": api_key,
        "target": spec["target"],
        "type": spec["format"],
        spec["param_key"]: item_id,
    }

    async with semaphore:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with session.get(BASE_URL, params=params) as response:
                    response.raise_for_status()
                    content = await response.text()

                    if spec["format"] == ResponseFormat.JSON:
                        return json.loads(content)
                    else:
                        return ET.fromstring(content)

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt == MAX_RETRIES:
                    logger.error(f"[{spec['db_name']}] 본문 요청 실패 (ID={item_id}): {e}")
                    return None
                
                wait_time = 2 ** attempt
                await asyncio.sleep(wait_time)
            except (json.JSONDecodeError, ET.ParseError) as e:
                logger.error(f"[{spec['db_name']}] 파싱 실패 (ID={item_id}): {e}")
                return None


async def collect_all_details(
    session: aiohttp.ClientSession,
    api_key: str,
    spec: DetailTargetSpec,
    ids: list[str],
    semaphore: asyncio.Semaphore,
) -> list[Any]:
    """추출된 모든 ID에 대해 병렬로 본문 데이터를 호출하고 결과를 병합합니다."""
    if not ids:
        return []

    logger.info(f"[{spec['db_name']}] {len(ids)}건의 본문 데이터 수집을 시작합니다...")
    
    tasks = [fetch_detail_item(session, api_key, spec, item_id, semaphore) for item_id in ids]
    
    # 병렬 처리 진행 (메모리 부족 시 청크 단위로 나누는 것을 고려해야 할 수 있습니다)
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    valid_results = []
    for idx, res in enumerate(results):
        if res is None:
            continue
        if isinstance(res, Exception):
            logger.error(f"[{spec['db_name']}] ID({ids[idx]}) 처리 중 예외 발생: {res}")
            continue
        
        valid_results.append(res)

    logger.info(f"[{spec['db_name']}] 총 {len(valid_results)}건 본문 수집 완료.")
    return valid_results


# ==============================================================================
# 4. 파일 통합 적재
# ==============================================================================

async def save_details_to_file(items: list[Any], spec: DetailTargetSpec) -> Path:
    """수집된 전체 본문 데이터를 단일 파일로 적재합니다."""
    partition_dir = DATA_DIR
    partition_dir.mkdir(parents=True, exist_ok=True)
    
    # idx_ 접두사 대신 body_ 접두사 사용
    file_name = f"body_{spec['db_name']}_{spec['target']}.{spec['format'].lower()}"
    file_path = partition_dir / file_name

    if not items:
        async with aiofiles.open(file_path, mode="w", encoding="utf-8") as f:
            await f.write("[]" if spec["format"] == ResponseFormat.JSON else f"<{spec['target']}DetailList/>")
        return file_path

    if spec["format"] == ResponseFormat.JSON:
        async with aiofiles.open(file_path, mode="w", encoding="utf-8") as f:
            await f.write(json.dumps(items, ensure_ascii=False, indent=2))

    elif spec["format"] == ResponseFormat.XML:
        root = ET.Element(f"{spec['target']}DetailList")
        for item_node in items:
            # item_node 자체는 파싱된 ET.Element 입니다. 자식으로 바로 붙입니다.
            root.append(item_node)
        
        ET.indent(root, space="  ", level=0)
        xml_str = ET.tostring(root, encoding="utf-8").decode("utf-8")
        
        async with aiofiles.open(file_path, mode="w", encoding="utf-8") as f:
            await f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            await f.write(xml_str)

    return file_path


# ==============================================================================
# 5. 파이프라인 오케스트레이션
# ==============================================================================

async def process_detail_target(
    session: aiohttp.ClientSession, 
    api_key: str, 
    spec: DetailTargetSpec, 
    semaphore: asyncio.Semaphore
) -> None:
    """단일 Target에 대한 [ID 추출 -> 본문 수집 -> 파일 적재] 과정을 제어합니다."""
    try:
        # 1. 인덱스 파일에서 ID 추출
        ids = await extract_ids_from_index(spec)
        
        if not ids:
            return
            
        # 2. 본문 데이터 수집
        detail_data = await collect_all_details(session, api_key, spec, ids, semaphore)
        
        # 3. 통합 파일 적재
        saved_path = await save_details_to_file(detail_data, spec)
        logger.info(f"[{spec['db_name']}] 본문 파일 적재 완료: {saved_path}")
            
    except Exception as e:
        logger.exception(f"[{spec['db_name']}] 파이프라인 처리 중 치명적 오류 발생: {e}")


async def run_pipeline() -> None:
    load_dotenv()
    api_key = os.getenv("LAW_OPEN_API_KEY")

    if not api_key:
        logger.error("환경변수 'LAW_OPEN_API_KEY'가 설정되어 있지 않습니다.")
        raise SystemExit(1)

# ⚠️ 중요: 각 Target에 맞게 id_field와 param_key를 맞춰주어야 합니다.
    target_dbs: list[DetailTargetSpec] = [
        {
            "db_name": "DB01", 
            "target": "law", 
            "description": "법령", 
            "format": ResponseFormat.JSON, 
            "id_field": "법령일련번호",      # 목록 API JSON 응답 내 식별자 키
            "param_key": "MST"              # 법령 본문 호출용 링크 파라미터
        },
        {
            "db_name": "DB03", 
            "target": "expc", 
            "description": "법령해석례", 
            "format": ResponseFormat.JSON, 
            "id_field": "법령해석례일련번호", # 기존 안건번호에서 실제 식별자로 수정
            "param_key": "ID"               # 해석례 본문 호출용 링크 파라미터
        },
        {
            "db_name": "DB10", 
            "target": "lstrm", 
            "description": "법령용어", 
            "format": ResponseFormat.JSON, 
            "id_field": "법령용어ID",         # 기존 용어일련번호에서 실제 식별자로 수정
            "param_key": "trmSeqs"          # 법령용어 본문 호출용 링크 파라미터
        },
        {
            "db_name": "DB19", 
            "target": "prec", 
            "description": "법원 판례", 
            "format": ResponseFormat.JSON, 
            "id_field": "판례일련번호",       # 판례 목록 API JSON 응답 내 식별자 키
            "param_key": "ID"               # 판례 본문 호출용 링크 파라미터
        },
    ]

    timeout = ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        tasks = [
            process_detail_target(session, api_key, spec, semaphore)
            for spec in target_dbs
        ]
        await asyncio.gather(*tasks)

    logger.info("모든 본문 데이터 적재 파이프라인 작업이 종료되었습니다.")

if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    asyncio.run(run_pipeline())