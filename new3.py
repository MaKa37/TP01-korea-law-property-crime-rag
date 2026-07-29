import json
import requests
import xml.etree.ElementTree as ET
import time
import re
import os

# 1. 처리할 파일 목록 및 대상(target) 타입 지정
files_to_process = {
    "Json_Files/DB01_law_parsed_ALL.json": "law",
    "Json_Files/DB03_expc_parsed_ALL.json": "expc",
    "Json_Files/DB10_admrul_parsed_ALL.json": "admrul"
}

base_url = "https://www.law.go.kr"

# 🔥 테스트용 제한 설정 (전체 데이터를 추출하려면 이 값을 None 으로 변경하세요)
MAX_ITEMS = 3 
# 서버 과부하 방지용 대기 시간 (초)
SLEEP_TIME = 0.5 

def extract_clean_text(xml_content, target):
    """XML 데이터에서 HTML 화면처럼 구조(조, 항, 호)를 살려 본문을 추출하는 함수"""
    try:
        root = ET.fromstring(xml_content)
        content_lines = []
        
        # [법령해석 (DB03)]
        if target == 'expc':
            for tag in ['질의요지', '회답', '이유']:
                node = root.find(f'.//{tag}')
                if node is not None and node.text:
                    content_lines.append(f"[{tag}]\n{node.text.strip()}")
                    
        # [법령 (DB01) & 행정규칙 (DB10)]
        else:
            # 1. 조문 핵심 내용 추출 (메타데이터 제외)
            jomuns = root.findall('.//조문단위')
            if jomuns:
                for node in jomuns:
                    jomun_texts = []
                    
                    # 조문단위 안의 모든 하위 태그를 순서대로 탐색
                    for elem in node.iter():
                        # 시스템 데이터(시행일자, 여부 등)는 버리고, 실제 '법 조항 텍스트'가 담기는 4가지 태그만 골라냄
                        if elem.tag in ['조문내용', '항내용', '호내용', '목내용']:
                            if elem.text and elem.text.strip():
                                jomun_texts.append(elem.text.strip())
                    
                    if jomun_texts:
                        # 한 조문 안의 조 -> 항 -> 호 내용을 줄바꿈(\n)으로 연결하여 가독성 확보
                        content_lines.append("\n".join(jomun_texts))
            else:
                if root.text and root.text.strip():
                    content_lines.append(root.text.strip())

            # 2. 부칙 추출 (법령 맨 뒤에 붙는 시행일 및 경과조치 등)
            buchiks = root.findall('.//부칙내용')
            if buchiks:
                content_lines.append("\n[부칙]")
                for buchik in buchiks:
                    if buchik.text and buchik.text.strip():
                        # 부칙 안의 불필요한 다중 공백만 가볍게 정제
                        cleaned_buchik = re.sub(r'\s+', ' ', buchik.text.strip())
                        content_lines.append(cleaned_buchik)
                        
        # 각 조문(제1조, 제2조...) 사이에는 두 줄 바꿈을 주어 확실히 구분
        return "\n\n".join(content_lines)
        
    except Exception as e:
        return f"XML 파싱 오류: {e}"

# 2. 메인 실행 루프
for filename, target in files_to_process.items():
    print(f"\n{'='*60}\n🚀 [{filename}] 본문 추출 시작...\n{'='*60}")
    
    # 저장할 새 파일 이름 생성 (예: DB01_law_parsed_ALL_with_text.json)
    save_filename = filename.replace(".json", "_with_text.json")
    extracted_results = []
    
    try:
        with open(filename, 'r', encoding='utf-8') as file:
            data = json.load(file)
            
            # 데이터가 딕셔너리 형태면 리스트로 변환
            items = list(data.values()) if isinstance(data, dict) else data
            
            # 테스트를 위해 개수 제한
            if MAX_ITEMS is not None:
                items = items[:MAX_ITEMS]
                
            for idx, item in enumerate(items):
                # 기존 데이터의 값들(Values)만 리스트로 추출
                values = list(item.values()) if isinstance(item, dict) else item
                
                # '/DRF'로 시작하는 링크 찾기
                api_path = next((v for v in values if isinstance(v, str) and v.startswith('/DRF')), None)
                
                extracted_text = ""
                
                if api_path:
                    # HTML 요청을 XML로 변환하여 완전한 URL 생성
                    xml_api_path = api_path.replace("type=HTML", "type=XML")
                    full_url = base_url + xml_api_path
                    
                    try:
                        print(f"[{idx+1}/{len(items)}] 데이터 요청 중...")
                        response = requests.get(full_url, timeout=10)
                        response.raise_for_status()
                        
                        # 함수를 호출하여 정제된 텍스트 추출
                        extracted_text = extract_clean_text(response.content, target)
                        
                        # API 서버를 위해 잠시 대기
                        time.sleep(SLEEP_TIME)
                        
                    except Exception as req_err:
                        extracted_text = f"요청 실패: {req_err}"
                        print(f" ⚠️ {extracted_text}")
                else:
                    extracted_text = "유효한 OpenAPI 링크가 없습니다."
                    
                # 3. 기존 데이터 구조와 추출된 본문을 합쳐서 새 딕셔너리 생성
                result_item = {
                    "original_data": item,
                    "extracted_text": extracted_text
                }
                extracted_results.append(result_item)
                
        # 4. 추출된 데이터를 새 JSON 파일로 저장
        with open(save_filename, 'w', encoding='utf-8') as outfile:
            # ensure_ascii=False 를 해야 한글이 깨지지 않고 제대로 저장됩니다.
            json.dump(extracted_results, outfile, ensure_ascii=False, indent=4)
            
        print(f"✅ 추출 완료! 파일이 저장되었습니다: {save_filename}")
        
    except FileNotFoundError:
        print(f"❌ 오류: '{filename}' 파일을 찾을 수 없습니다. 경로를 확인해주세요.")
    except Exception as e:
        print(f"❌ 예상치 못한 오류 발생: {e}")