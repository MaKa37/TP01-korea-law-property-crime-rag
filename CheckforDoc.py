import json
import requests
import xml.etree.ElementTree as ET

file_list = [
    "Json_Files/DB01_law_parsed_ALL.json",
    "Json_Files/DB03_expc_parsed_ALL.json",
    "Json_Files/DB10_admrul_parsed_ALL.json",
    "Json_Files/DB19_prec_parsed_ALL.json"
]

base_url = "https://www.law.go.kr"

# XML의 계층 구조를 보기 좋게 출력하는 재귀 함수
def print_xml_structure(element, indent=0):
    # 태그 이름 출력
    print("  " * indent + f"<{element.tag}>")
    
    # 중복 출력 방지를 위해 자식 태그의 이름만 수집
    child_tags = []
    for child in element:
        if child.tag not in child_tags:
            child_tags.append(child.tag)
            print_xml_structure(child, indent + 1)

for filename in file_list:
    print(f"\n{'='*60}")
    print(f"[{filename}] 구조 확인")
    print(f"{'='*60}")
    
    try:
        with open(filename, 'r', encoding='utf-8') as file:
            data = json.load(file)
            first_item = data[0] if isinstance(data, list) else list(data.values())[0]
            values = list(first_item.values()) if isinstance(first_item, dict) else first_item
            api_path = values[-1]
            
            if str(api_path).startswith("/DRF"):
                # ★ 핵심: 구조를 보기 위해 HTML을 XML로 강제 변경
                xml_api_path = api_path.replace("type=HTML", "type=XML")
                full_url = base_url + xml_api_path
                
                print(f"요청 URL: {full_url}\n")
                
                response = requests.get(full_url)
                response.raise_for_status()
                
                # XML 파싱
                root = ET.fromstring(response.content)
                
                # 구조 출력 (최상위 태그부터)
                print("[데이터 구조 트리]")
                print_xml_structure(root)
                
    except Exception as e:
        print(f"데이터를 가져오거나 분석하는 중 오류가 발생했습니다: {e}")