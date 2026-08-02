"""법제처 국가법령정보 공동활용 Open API 클라이언트.

국가법령정보센터(law.go.kr)의 lawSearch API를 호출하여 지정된 target(법령,
법령용어, 판례 등)의 검색 결과 중 첫 번째 항목을 조회하는 스모크 테스트용
클라이언트 모듈이다.

이 API는 target에 따라 지원하는 응답 포맷이 다르다(JSON만 지원하는 target,
XML만 지원하는 target이 존재). 본 모듈은 두 포맷을 모두 파싱해 동일한
``LawItem``(= ``dict[str, Any]``) 형태로 반환함으로써, 호출부가 포맷 차이를
신경 쓰지 않도록 추상화한다.

요구사항:
    Python >= 3.11 (``enum.StrEnum`` 사용)
    requests
    python-dotenv

환경 변수:
    LAW_OPEN_API_KEY: 법제처 Open API에서 발급받은 기관코드(OC). 필수.
        발급: https://open.law.go.kr

사용 예시:
    .. code-block:: bash

        $ export LAW_OPEN_API_KEY=your_oc_value
        $ python law_api_client.py

    .. code-block:: python

        from law_api_client import fetch_first_json_item

        item = fetch_first_json_item(api_key="your_oc_value", target_name="law")
        print(item)

참고:
    법제처 Open API 가이드: https://open.law.go.kr/LSO/openApi/guideList.do

주의(검증 필요):
    XML 응답의 태그 구조는 공식 문서를 기준으로 작성했으나 target별로 실제
    스키마가 다를 수 있다. 새 target을 추가할 때는 반드시 실제 응답 샘플로
    ``fetch_first_xml_item``의 파싱 로직을 검증할 것 (자세한 내용은
    API_SPEC.md의 "8. 알려진 제약 및 검증 필요 사항" 참고).
"""

import json
import logging
import os
import xml.etree.ElementTree as ET
from enum import StrEnum
from typing import Any, Callable, TypeAlias, TypedDict

import requests
from dotenv import load_dotenv

__version__ = "1.2.0"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

#: lawSearch API 엔드포인트. target 파라미터로 검색 대상(법령/판례/용어 등)을 지정한다.
BASE_URL = "https://www.law.go.kr/DRF/lawSearch.do"

#: 요청 타임아웃(초). 네트워크 지연 시 프로세스가 무한 대기하지 않도록 설정한다.
REQUEST_TIMEOUT = 10

#: 조회 결과 한 건을 나타내는 dict 별칭. target마다 필드 구성이 달라 별도의
#: TypedDict 대신 범용 별칭을 사용해 함수 시그니처를 간결하게 유지한다.
LawItem: TypeAlias = dict[str, Any]


class ResponseFormat(StrEnum):
    """lawSearch API가 지원하는 응답 포맷.

    target마다 지원 포맷이 다르다(예: 대부분의 target은 JSON을 지원하지만,
    일부 target은 XML만 지원). ``str``을 상속하므로 요청 쿼리 파라미터에
    바로 사용할 수 있고, 순수 문자열("JSON", "XML")과 값 기준으로 동일하게
    비교·해시되어 기존 코드와도 호환된다.
    """

    JSON = "JSON"
    XML = "XML"


class TargetSpec(TypedDict):
    """조회할 target 하나를 기술하는 설정 레코드.

    Attributes:
        db_name: 내부 관리용 DB 식별자 (로그/출력 용도).
        target: API의 target 파라미터 값 (예: "law", "prec", "lstrm").
        description: target에 대한 한글 설명 (출력 용도).
        format: 이 target을 조회할 때 사용할 응답 포맷.
    """

    db_name: str
    target: str
    description: str
    format: ResponseFormat


class LawApiError(Exception):
    """법령 API 호출 또는 응답 파싱 과정에서 발생하는 오류.

    ``requests`` 및 표준 라이브러리가 던지는 다양한 예외
    (``Timeout``, ``ConnectionError``, ``HTTPError``, ``JSONDecodeError``,
    ``xml.etree.ElementTree.ParseError`` 등)를 이 예외 하나로 정규화하여,
    호출부가 "법령 API 관련 오류"만 잡으면 되도록 한다. 원인 예외는
    ``raise ... from e``로 체이닝되어 ``__cause__``를 통해 추적 가능하다.
    """


# --------------------------------------------------------------------------
# API 호출
# --------------------------------------------------------------------------


def _request(
    api_key: str,
    target_name: str,
    response_format: ResponseFormat,
) -> requests.Response:
    """lawSearch API에 공통 GET 요청을 보낸다.

    display=1, page=1로 고정하는 이유:
        이 클라이언트는 특정 target이 정상적으로 응답하는지 확인하는
        스모크 테스트 용도이므로, 페이지네이션 전체를 순회할 필요 없이
        첫 번째 결과 한 건만 확인하면 충분하다. 페이지네이션 전체 순회가
        필요하다면 이 함수가 아닌 별도 함수를 작성해야 한다.

    Args:
        api_key: 법제처에서 발급받은 기관코드(OC).
        target_name: API target 파라미터 값 (예: "law", "prec").
        response_format: 응답 포맷 (JSON 또는 XML).

    Returns:
        성공한 ``requests.Response`` 객체 (2xx 상태 코드 보장).

    Raises:
        LawApiError: 타임아웃, 연결 실패, 4xx/5xx HTTP 에러 발생 시.
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
        raise LawApiError(
            f"요청 시간 초과 (target={target_name}, timeout={REQUEST_TIMEOUT}s): {e}"
        ) from e
    except requests.exceptions.ConnectionError as e:
        raise LawApiError(f"네트워크 연결 실패 (target={target_name}): {e}") from e
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "unknown"
        raise LawApiError(
            f"HTTP 에러 (target={target_name}, status={status}): {e}"
        ) from e

    return response


def fetch_first_json_item(api_key: str, target_name: str) -> LawItem:
    """JSON 응답을 지원하는 target(law, prec, lstrm 등)의 첫 결과를 조회한다.

    응답 구조 가정:
        ``{ "<root key>": { "<target_name>": [...] 또는 {...} } }``

        법제처 API는 검색 결과가 1건이면 ``target_name`` 키의 값이 dict,
        2건 이상이면 list로 내려오는 것으로 관찰되어, 두 타입을 모두
        방어적으로 처리한다. root key 자체는 target마다 이름이 달라질 수
        있어 첫 번째 키를 그대로 사용한다.

    Args:
        api_key: 법제처에서 발급받은 기관코드(OC).
        target_name: JSON을 지원하는 API target 값.

    Returns:
        검색 결과의 첫 번째 항목. 데이터가 없으면 ``{"error": "..."}``
        형태로 반환한다 (예외를 던지지 않음 — "결과 없음"은 정상적인
        비즈니스 케이스이지 시스템 오류가 아니므로).

    Raises:
        LawApiError: 네트워크 오류, HTTP 오류, 또는 응답이 유효한 JSON이
            아니거나 최상위 구조가 비어 있는 경우.

    Example:
        >>> fetch_first_json_item(api_key="test_oc", target_name="law")
        {'법령ID': '001766', '법령명한글': '...', ...}
    """
    response = _request(api_key, target_name, ResponseFormat.JSON)

    try:
        payload = response.json()
    except json.JSONDecodeError as e:
        raise LawApiError(
            f"JSON 파싱 실패 (target={target_name}): 응답이 JSON 형식이 아닙니다. {e}"
        ) from e

    if not payload:
        raise LawApiError(f"빈 응답 (target={target_name})")

    # root key 이름은 target마다 다르므로(예: "LawSearch", "PrecSearch"),
    # 별도 매핑 없이 첫 번째(=유일한) 키를 그대로 사용한다.
    root_key = next(iter(payload), None)
    if root_key is None:
        raise LawApiError(f"응답에 root key가 없습니다 (target={target_name})")

    target_data = payload[root_key]
    results = target_data.get(target_name) if isinstance(target_data, dict) else None

    # 결과 0건: 키 자체가 없거나 빈 리스트 → 아래 폴백으로 처리.
    # 결과 1건: dict, 결과 2건 이상: list — 두 케이스를 모두 방어적으로 처리한다.
    if isinstance(results, list) and results:
        return results[0]
    if isinstance(results, dict):
        return results

    return {"error": f"{target_name} 데이터를 찾을 수 없습니다."}


def fetch_first_xml_item(api_key: str, target_name: str) -> LawItem:
    """XML 응답만 지원하는 target의 첫 결과를 조회한다.

    법제처 API 중 일부 target은 JSON을 지원하지 않고 XML만 반환하므로
    별도 파싱 경로가 필요하다.

    XML 구조 가정:
        ``<target명>`` 태그가 결과 항목 하나를 감싸는 구조를 가정한다.
        예: ``<LawSearch><law>...</law><law>...</law></LawSearch>``.
        실제 최상위 태그명이 다를 경우, root의 첫 번째 자식 요소로
        폴백한다. 이 가정은 target마다 다를 수 있으므로 신규 target
        추가 시 반드시 실제 응답으로 검증해야 한다 (API_SPEC.md 8절 참고).

    Args:
        api_key: 법제처에서 발급받은 기관코드(OC).
        target_name: XML만 지원하는 API target 값.

    Returns:
        검색 결과 첫 번째 항목의 각 필드를 ``{태그명: 텍스트}``로 매핑한
        dict. 데이터가 없으면 ``{"error": "..."}``를 반환한다.

    Raises:
        LawApiError: 네트워크/HTTP 오류, 또는 응답이 유효한 XML이 아닌 경우.

    Example:
        >>> fetch_first_xml_item(api_key="test_oc", target_name="expc")
        {'법령해석례ID': '12345', '안건명': '...', ...}
    """
    response = _request(api_key, target_name, ResponseFormat.XML)

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as e:
        raise LawApiError(f"XML 파싱 실패 (target={target_name}): {e}") from e

    first_item = root.find(f".//{target_name}")

    # 주의: ElementTree의 Element는 자식이 없으면 bool(element)가 False로
    # 평가된다(len(element) == 0 이기 때문). 따라서 `or`로 폴백을 연결하면
    # "찾았지만 자식이 없는 요소"까지 못 찾은 것으로 오판할 수 있어,
    # 반드시 `is None`으로 명시적으로 비교해야 한다.
    if first_item is None:
        first_item = next(iter(root), None)

    if first_item is None:
        return {"error": f"{target_name} 데이터를 찾을 수 없습니다."}

    return {child.tag: (child.text or "").strip() for child in first_item}


#: 포맷(JSON/XML)에 따라 사용할 fetch 함수를 매핑한다.
#: 새 포맷을 추가할 경우 이 매핑에만 등록하면 되며, fetch_first_item()이나
#: main()의 분기 로직은 수정할 필요가 없다 (개방-폐쇄 원칙).
_FETCHERS: dict[ResponseFormat, Callable[[str, str], LawItem]] = {
    ResponseFormat.JSON: fetch_first_json_item,
    ResponseFormat.XML: fetch_first_xml_item,
}


def fetch_first_item(api_key: str, spec: TargetSpec) -> LawItem:
    """``TargetSpec``에 명시된 포맷에 맞는 fetch 함수로 위임한다.

    Args:
        api_key: 법제처에서 발급받은 기관코드(OC).
        spec: 조회할 target 설정.

    Returns:
        조회 결과 dict. 알 수 없는 포맷이거나 API 오류가 발생하면
        ``{"error": "..."}`` 형태로 반환한다 (예외를 밖으로 전파하지 않음 —
        ``main()``에서 여러 target을 순차 처리할 때 하나의 실패가 전체를
        중단시키지 않도록 하기 위함).
    """
    fetch_fn = _FETCHERS.get(spec["format"])
    if fetch_fn is None:
        message = f"지원하지 않는 포맷입니다: {spec['format']}"
        logger.error(message)
        return {"error": message}

    try:
        return fetch_fn(api_key, spec["target"])
    except LawApiError as e:
        logger.error(str(e))
        return {"error": str(e)}


def load_api_key() -> str:
    """환경 변수에서 API 키를 로드하고 검증한다.

    Returns:
        검증된 API 키 문자열.

    Raises:
        SystemExit: ``LAW_OPEN_API_KEY``가 설정되어 있지 않은 경우.
            (키 없이 진행하면 이후 모든 target 조회가 실패하므로,
            네트워크 요청을 시도하기 전에 조기 종료한다.)
    """
    load_dotenv()
    api_key = os.getenv("LAW_OPEN_API_KEY")

    if not api_key:
        logger.error("환경변수 LAW_OPEN_API_KEY가 설정되어 있지 않습니다.")
        raise SystemExit(1)

    return api_key


# --------------------------------------------------------------------------
# 출력
# --------------------------------------------------------------------------

_SECTION_DIVIDER = "=" * 60


def _print_section_header(spec: TargetSpec) -> None:
    """조회 대상 target을 구분선과 함께 출력한다."""
    print(f"\n{_SECTION_DIVIDER}")
    print(f"📌 {spec['db_name']} ({spec['description']}) - {spec['format']} 방식으로 호출")
    print(_SECTION_DIVIDER)


def _print_item(item: LawItem) -> None:
    """조회 결과 한 건을 보기 좋게 정렬된 JSON 문자열로 출력한다."""
    print(json.dumps(item, ensure_ascii=False, indent=4))


def main() -> None:
    """설정된 target 목록을 순회하며 각 target의 첫 결과를 조회, 출력한다."""
    api_key = load_api_key()

    target_dbs: list[TargetSpec] = [
        {
            "db_name": "DB01",
            "target": "law",
            "description": "법령",
            "format": ResponseFormat.JSON,
        },
        {
            "db_name": "DB10",
            "target": "lstrm",
            "description": "법령용어",
            "format": ResponseFormat.JSON,
        },
        {
            "db_name": "DB19",
            "target": "prec",
            "description": "법원 판례",
            "format": ResponseFormat.JSON,
        },
    ]

    for spec in target_dbs:
        _print_section_header(spec)
        first_item = fetch_first_item(api_key, spec)
        _print_item(first_item)


if __name__ == "__main__":
    main()