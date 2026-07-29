import json
import requests
import xml.etree.ElementTree as ET

filename = "Json_Files/DB19_prec_parsed_ALL.json"
base_url = "https://www.law.go.kr"

print(f"\n{'='*60}\n[{filename}] 본문 데이터 추출 (예외처리 적용)\n{'='*60}")

try:
    with open(filename, 'r', encoding='utf-8') as file:
        data = json.load(file)
        
        # 데이터가 리스트라면 첫 번째 요소 추출, 아니면 그대로 사용
        first_item = data[0] if isinstance(data, list) else data
        
        # 1. 링크 주소 찾기
        api_path = first_item.get("판례상세링크", "")
        
        if api_path and api_path.startswith("/DRF"):
            xml_api_path = api_path.replace("type=HTML", "type=XML")
            full_url = base_url + xml_api_path
            print(f"🔗 요청 URL: {full_url}\n")
            
            response = requests.get(full_url)
            response.raise_for_status()
            
            # 2. XML 파싱 및 에러 메시지 감지
            root = ET.fromstring(response.content)
            
            # <Law> 태그 안에 '일치하는 판례가 없습니다' 텍스트가 있는지 확인
            if root.tag == 'Law' and root.text and '일치하는 판례가 없습니다' in root.text:
                print("⚠️ [API 거부/외부출처 데이터] 상세 본문을 제공하지 않는 판례입니다.")
                print("🔄 대안(Fallback): JSON 메타데이터를 본문으로 대체합니다.\n")
                
                # JSON이 가진 정보로 가상의 본문을 생성
                fallback_text = (
                    f"■ 사건명: {first_item.get('사건명', '정보없음')}\n"
                    f"■ 사건번호: {first_item.get('사건번호', '정보없음')}\n"
                    f"■ 선고일자: {first_item.get('선고일자', '정보없음')}\n"
                    f"■ 데이터출처: {first_item.get('데이터출처명', '정보없음')}"
                )
                print("[추출된 대체 본문]")
                print(fallback_text)
                
            else:
                # 정상적으로 데이터를 가져온 경우 (판결요지 등 추출)
                print("✅ 정상 판례입니다. 본문을 추출합니다.")
                # 예: 판결요지나 판시사항 등을 가져오는 로직 (필요시 추가)
                # content = root.find('.//판결요지')
                
        else:
            print("⚠️ 오류: 유효한 OpenAPI 링크를 찾을 수 없습니다.")

except Exception as e:
    print(f"오류 발생: {e}")