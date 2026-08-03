"""법제처 국가법령정보 Open API 연동 클라이언트.

법제처 국가법령정보센터(law.go.kr)의 lawSearch API를 호출하여 법령, 용어, 판례 등의
검색 결과를 단건(첫 번째 항목) 조회하는 통합 테스트용 모듈입니다.

API 응답 포맷(JSON/XML)의 차이를 내부적으로 추상화하여, 
호출부에서는 포맷에 관계없이 동일한 Dictionary(`LawItem`) 형태로 결과를 반환받습니다.
최종 출력 단계에서는 요청한 원본 포맷(JSON/XML)으로 직렬화하여 제공합니다.

[실행 환경 및 요구사항]
    - Python: >= 3.11 (enum.StrEnum, xml.etree.ElementTree.indent 활용)
    - Dependencies: requests, python-dotenv

[환경 변수]
    - LAW_OPEN_API_KEY: 법제처 Open API 기관코드(OC) (발급처: https://open.law.go.kr)
"""

import json
import logging
import os
import xml.etree.ElementTree as ET
from enum import StrEnum
from typing import Any, Callable, TypeAlias, TypedDict

import requests
from dotenv import load_dotenv

__version__ = "1.3.0"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# API 엔드포인트 및 네트워크 설정
BASE_URL = "https://www.law.go.kr/DRF/lawSearch.do"
REQUEST_TIMEOUT = 10

# 단일 조회 결과 데이터를 담는 범용 타입 별칭
LawItem: TypeAlias = dict[str, Any]


class ResponseFormat(StrEnum):
    """API 응답 포맷 정의 (API 요청 시 'type' 파라미터 값과 매핑)"""
    JSON = "JSON"
    XML = "XML"


class TargetSpec(TypedDict):
    """API 호출 대상(Target) 메타데이터 명세"""
    db_name: str         # 내부 관리용 DB 식별자 (예: DB01)
    target: str          # API target 파라미터 값 (예: 'law', 'prec')
    description: str     # 대상에 대한 한글 설명
    format: ResponseFormat


class LawApiError(Exception):
    """API 통신 및 데이터 파싱 과정에서 발생하는 예외를 래핑(Wrapping)하는 사용자 정의 예외"""
    pass


# --------------------------------------------------------------------------
# API 통신 및 데이터 파싱 로직
# --------------------------------------------------------------------------

def _request(
    api_key: str,
    target_name: str,
    response_format: ResponseFormat,
) -> requests.Response:
    """법제처 API에 GET 요청을 전송합니다.

    Args:
        api_key: 발급받은 Open API 기관코드(OC).
        target_name: 조회 대상 (예: 'law').
        response_format: 응답받을 데이터 포맷 (JSON 또는 XML).

    Returns:
        requests.Response: HTTP 응답 객체.

    Raises:
        LawApiError: 타임아웃, 네트워크 연결 실패, 또는 4xx/5xx HTTP 에러 발생 시.
    """
    params = {
        "OC": api_key,
        "target": target_name,
        "type": response_format,
        "display": 1,
        "page": 1,
    }

    try:
        response = requests.get(BASE_URL, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.exceptions.Timeout as e:
        raise LawApiError(f"요청 시간 초과 (target={target_name}): {e}") from e
    except requests.exceptions.ConnectionError as e:
        raise LawApiError(f"네트워크 연결 실패 (target={target_name}): {e}") from e
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "unknown"
        raise LawApiError(f"HTTP 에러 (status={status}, target={target_name}): {e}") from e

    return response


def fetch_first_json_item(api_key: str, target_name: str) -> LawItem:
    """JSON 포맷으로 API를 호출하여 첫 번째 검색 결과를 추출합니다.

    Args:
        api_key: Open API 기관코드(OC).
        target_name: 조회 대상 (예: 'law').

    Returns:
        LawItem: 추출된 단건 데이터 딕셔너리. 에러 또는 데이터 누락 시 'error' 키를 포함합니다.
    
    Raises:
        LawApiError: JSON 디코딩 실패 또는 최상위 노드 식별 실패 시.
    """
    response = _request(api_key, target_name, ResponseFormat.JSON)

    try:
        payload = response.json()
    except json.JSONDecodeError as e:
        raise LawApiError(f"JSON 파싱 실패 (target={target_name}): {e}") from e

    if not payload:
        raise LawApiError(f"빈 응답 (target={target_name})")

    # [동적 키 추출] 타겟(target)마다 상이한 최상위(Root) 키를 동적으로 추출 (예: LawSearch, PrecSearch 등)
    root_key = next(iter(payload), None)
    if root_key is None:
        raise LawApiError(f"응답에 최상위 키(Root key)가 존재하지 않습니다. (target={target_name})")

    target_data = payload[root_key]
    results = target_data.get(target_name) if isinstance(target_data, dict) else None

    # [비일관적 스펙 대응] API 응답 건수가 1건일 경우 dict, 복수일 경우 list로 반환되는 구조적 문제 방어
    if isinstance(results, list) and results:
        return results[0]
    if isinstance(results, dict):
        return results

    return {"error": f"'{target_name}' 데이터를 찾을 수 없습니다."}


def fetch_first_xml_item(api_key: str, target_name: str) -> LawItem:
    """XML 포맷으로 API를 호출하여 첫 번째 검색 결과를 딕셔너리로 변환합니다.

    Args:
        api_key: Open API 기관코드(OC).
        target_name: 조회 대상 (예: 'law').

    Returns:
        LawItem: 추출 및 변환된 단건 데이터 딕셔너리.
    
    Raises:
        LawApiError: XML 파싱 에러 발생 시.
    """
    response = _request(api_key, target_name, ResponseFormat.XML)

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as e:
        raise LawApiError(f"XML 파싱 실패 (target={target_name}): {e}") from e

    # <target_name> 태그를 최우선 탐색하며, 존재하지 않을 경우 최상위 노드의 첫 번째 자식을 폴백(Fallback)으로 사용
    first_item = root.find(f".//{target_name}")
    if first_item is None:
        first_item = next(iter(root), None)

    if first_item is None:
        return {"error": f"'{target_name}' 데이터를 찾을 수 없습니다."}

    # Element 객체의 자식 노드들을 순회하며 {태그명: 텍스트} 형태의 딕셔너리로 변환
    return {child.tag: (child.text or "").strip() for child in first_item}


# 포맷별 파서(Fetcher) 매핑 
# 향후 새로운 포맷(예: CSV, YAML 등) 추가 시 이 딕셔너리만 확장하도록 OCP(개방-폐쇄 원칙) 준수
_FETCHERS: dict[ResponseFormat, Callable[[str, str], LawItem]] = {
    ResponseFormat.JSON: fetch_first_json_item,
    ResponseFormat.XML: fetch_first_xml_item,
}


def fetch_first_item(api_key: str, spec: TargetSpec) -> LawItem:
    """TargetSpec에 정의된 포맷에 맞춰 적절한 파서(Fetcher)를 라우팅하여 데이터를 조회합니다.

    Args:
        api_key: Open API 기관코드.
        spec: 호출 대상의 메타데이터가 담긴 설정 레코드.

    Returns:
        LawItem: 조회된 단건 데이터. 파싱 또는 라우팅 실패 시 'error' 키를 포함합니다.
    """
    fetch_fn = _FETCHERS.get(spec["format"])
    
    if fetch_fn is None:
        msg = f"지원하지 않는 응답 포맷입니다: {spec['format']}"
        logger.error(msg)
        return {"error": msg}

    try:
        return fetch_fn(api_key, spec["target"])
    except LawApiError as e:
        logger.error(str(e))
        return {"error": str(e)}


def load_api_key() -> str:
    """시스템 환경 변수 또는 .env 파일에서 API 키를 로드합니다.
    
    Returns:
        str: 로드된 API 키 문자열.
        
    Raises:
        SystemExit: API 키가 설정되어 있지 않을 경우 프로그램을 종료합니다.
    """
    load_dotenv()
    api_key = os.getenv("LAW_OPEN_API_KEY")

    if not api_key:
        logger.error("환경변수 'LAW_OPEN_API_KEY'가 설정되어 있지 않습니다.")
        raise SystemExit(1)

    return api_key


# --------------------------------------------------------------------------
# 출력 포맷팅 및 메인 실행(Entry Point)
# --------------------------------------------------------------------------

_SECTION_DIVIDER = "=" * 80


def _print_section_header(spec: TargetSpec) -> None:
    """터미널 출력을 위한 조회 대상 섹션 헤더를 생성합니다."""
    print(f"\n{_SECTION_DIVIDER}")
    print(f"📌 {spec['db_name']} ({spec['description']}) - {spec['format']} 요청")
    print(_SECTION_DIVIDER)


def _print_item(item: LawItem, fmt: ResponseFormat, target_name: str) -> None:
    """Dictionary 구조로 추상화된 응답 데이터를 요청했던 원본 포맷(JSON/XML)으로 직렬화하여 출력합니다.

    Args:
        item: 파싱이 완료된 단일 법령 데이터 딕셔너리.
        fmt: 직렬화할 목표 포맷 (ResponseFormat).
        target_name: XML 렌더링 시 최상위 태그명으로 사용할 타겟 문자열.
    """
    # 에러 페이로드가 포함된 경우, 포맷에 관계없이 가독성을 위해 JSON 형태로 출력
    if "error" in item:
        print(json.dumps(item, ensure_ascii=False, indent=4))
        return

    if fmt == ResponseFormat.JSON:
        print(json.dumps(item, ensure_ascii=False, indent=4))
        
    elif fmt == ResponseFormat.XML:
        # Dictionary 데이터를 기반으로 XML Element Tree 재구성
        root = ET.Element(target_name)
        for key, value in item.items():
            child = ET.SubElement(root, key)
            child.text = str(value)
        
        # 가독성을 위한 들여쓰기(Indentation) 적용 (Python 3.9+ 지원)
        ET.indent(root, space="    ", level=0)
        xml_str = ET.tostring(root, encoding="unicode")
        print(xml_str)


def main() -> None:
    """메인 실행부: 설정된 대상(Target) 목록을 순회하며 첫 번째 검색 결과를 출력합니다."""
    api_key = load_api_key()

    # 테스트 대상 DB 메타데이터 정의
    target_dbs: list[TargetSpec] = [
        {"db_name": "DB01", "target": "law", "description": "법령", "format": ResponseFormat.JSON},
        {"db_name": "DB03", "target": "expc", "description": "법령해석례", "format": ResponseFormat.JSON},
        {"db_name": "DB10", "target": "lstrm", "description": "법령용어", "format": ResponseFormat.JSON},
        {"db_name": "DB19", "target": "prec", "description": "법원 판례", "format": ResponseFormat.JSON},
    ]

    for spec in target_dbs:
        _print_section_header(spec)
        first_item = fetch_first_item(api_key, spec)
        _print_item(first_item, spec["format"], spec["target"])


if __name__ == "__main__":
    main()