import xml.etree.ElementTree as ET
import json
import requests
import time
from dotenv import load_dotenv
import os

class LegalDataParser:
    """법제처 및 타부처 공공데이터 XML 파서 및 직렬화 클래스"""

    @staticmethod
    def _clean_xml(xml_string):
        """비표준 헤더를 제거하고 유효한 XML 문자열만 추출합니다."""
        start_idx = xml_string.find('<?xml')
        if start_idx != -1:
            return xml_string[start_idx:]
        return xml_string

    @staticmethod
    def get_total_count(xml_string):
        """XML 응답에서 총 데이터 건수(totalCnt)를 추출합니다."""
        clean_xml = LegalDataParser._clean_xml(xml_string)
        try:
            root = ET.fromstring(clean_xml)
            total_cnt = root.find('.//totalCnt')
            if total_cnt is not None and total_cnt.text.isdigit():
                return int(total_cnt.text)
        except ET.ParseError as e:
            print(f"XML Parsing Error (totalCnt): {e}")
        return 0

    @staticmethod
    def parse_to_dict(xml_string, target_tag):
        """XML 문자열을 파싱하여 타겟 태그의 데이터를 딕셔너리 리스트로 직렬화합니다."""
        clean_xml = LegalDataParser._clean_xml(xml_string)
        
        try:
            root = ET.fromstring(clean_xml)
        except ET.ParseError as e:
            print(f"XML Parsing Error: {e}")
            return []

        result_list = []
        
        for item in root.findall(f'.//{target_tag}'):
            item_data = {}
            
            if 'id' in item.attrib:
                item_data['id'] = item.attrib['id']
                
            for child in item:
                item_data[child.tag] = child.text.strip() if child.text else ""
                
            result_list.append(item_data)
            
        return result_list


def fetch_data(api_url, params):
    """API를 호출하여 데이터를 받아오고 XML 텍스트를 바로 반환합니다."""
    try:
        response = requests.get(api_url, params=params)
        response.raise_for_status()
        return response.text
    except requests.exceptions.RequestException as e:
        print(f"API 호출 실패: {e}")
        return None


# ==========================================
# 실행부 (전체 데이터 수집 로직)
# ==========================================
if __name__ == "__main__":
    load_dotenv()
    API_KEY = os.getenv("LAW_OPEN_API_KEY")
    
    # 💡 [수정 1] 법제처 통합 목록 조회 엔드포인트는 일반적으로 'lawSearch.do'를 사용합니다.
    BASE_URL = "https://www.law.go.kr/DRF/lawSearch.do"
    
    TARGET_NAME = "prec"   # API 규격에 맞는 소문자 target명 (law, expc, admrul 등)
    TAG_NAME = "prec"      # XML 응답 내부의 반복 데이터 태그명 (법제처 open API 기준)
    DB_NAME = "DB19"
    
    # API 요청 기본 파라미터 세팅
    request_params = {
        'OC': API_KEY,      
        'target': TARGET_NAME,    
        'type': 'XML',      
        'display': 100,     
        'page': 1           
    }
    
    all_parsed_data = []
    total_count = 0
    current_page = 1
    
    print(f"[{TARGET_NAME.upper()} 데이터 수집을 시작합니다...]")
    
    while True:
        request_params['page'] = current_page
        
        xml_data = fetch_data(BASE_URL, request_params)
        
        if not xml_data:
            print("데이터를 가져오지 못해 수집을 중단합니다.")
            break
            
        # 첫 번째 페이지에서 총 데이터 건수(totalCnt) 확인
        if current_page == 1:
            total_count = LegalDataParser.get_total_count(xml_data)
            print(f"▶ 총 검색된 데이터 개수: {total_count}건")
            if total_count == 0:
                print("수집할 데이터가 없거나 API 파라미터/키를 확인해주세요.")
                break

        # 💡 [수정 2] 고정된 serialize 메서드 대신 공용 파서에 올바른 태그 이름(expc)을 직접 전달
        parsed_data = LegalDataParser.parse_to_dict(xml_data, TAG_NAME)
        
        if not parsed_data:
            print("더 이상 파싱할 데이터가 없습니다.")
            break
            
        all_parsed_data.extend(parsed_data)
        print(f"Page {current_page} 파싱 완료 (누적: {len(all_parsed_data)} / {total_count})")
        
        if len(all_parsed_data) >= total_count:
            print("모든 데이터를 성공적으로 수집했습니다.")
            break
            
        current_page += 1
        time.sleep(0.5)

    # 3. 수집된 전체 데이터를 단일 JSON 파일로 변환 및 저장
    if all_parsed_data:
        json_data = json.dumps(all_parsed_data, ensure_ascii=False, indent=4)
        
        # 💡 [수정 3] 파이썬 변수 f-string 문법 오류 수정 (f"문자열{변수}")
        final_json_filename = f"{DB_NAME}_{TARGET_NAME}_parsed_ALL.json"
        with open(final_json_filename, 'w', encoding='utf-8') as f:
            f.write(json_data)
            
        print(f"\n✅ 전체 JSON 파싱 파일 저장 완료: {final_json_filename}")
        print(f"총 저장된 데이터 건수: {len(all_parsed_data)}건")