import json
import requests
import xml.etree.ElementTree as ET

# 문제가 발생한 2개 파일만 테스트
file_list = [
    "Json_Files/DB10_admrul_parsed_ALL.json",
    "Json_Files/DB19_prec_parsed_ALL.json"
]

base_url = "https://www.law.go.kr"

def print_xml_structure(element, indent=0):
    print("  " * indent + f"<{element.tag}>")
    child_tags = []
    for child in element:
        if child.tag not in child_tags:
            child_tags.append(child.tag)
            print_xml_structure(child, indent + 1)

for filename in file_list:
    print(f"\n{'='*60}")
    print(f"[{filename}] 상세 디버깅")
    print(f"{'='*60}")
    
    try:
        with open(filename, 'r', encoding='utf-8') as file:
            data = json.load(file)
            first_item = data[0] if isinstance(data, list) else list(data.values())[0]
            values = list(first_item.values()) if isinstance(first_item, dict) else first_item
            
            # 🔥수정 포인트 1: 인덱스에 의존하지 않고 '/DRF'로 시작하는 링크 문자열을 직접 찾음
            api_path = next((v for v in values if isinstance(v, str) and v.startswith('/DRF')), None)
            
            if api_path:
                xml_api_path = api_path.replace("type=HTML", "type=XML")
                full_url = base_url + xml_api_path
                print(f"🔗 요청 URL: {full_url}\n")
                
                response = requests.get(full_url)
                response.raise_for_status()
                
                # XML 파싱
                root = ET.fromstring(response.content)
                
                # 🔥수정 포인트 2: 하위 태그가 없는 경우(에러 응답 등) 원본 텍스트를 출력해서 이유 확인
                if len(list(root)) == 0:
                    print("⚠️ 경고: 구조(하위 태그)가 없습니다. API 서버에서 데이터를 주지 않았습니다.")
                    print(f"응답된 내용(에러메시지 등): {response.text}")
                else:
                    print("[데이터 구조 트리]")
                    print_xml_structure(root)
            else:
                print("⚠️ 오류: 리스트 안에서 '/DRF' 로 시작하는 링크를 찾을 수 없습니다.")
                print(f"확인된 데이터: {values}")
                
    except Exception as e:
        print(f"오류 발생: {e}")