import json

# 이미지에 나열된 JSON 파일명 리스트
file_list = [
    "Json_Files\DB01_law_parsed_ALL.json",
    "Json_Files\DB03_expc_parsed_ALL.json",
    "Json_Files\DB10_admrul_parsed_ALL.json",
    "Json_Files\DB19_prec_parsed_ALL.json"
]

for filename in file_list:
    print(f"\n=== {filename}의 첫 번째 딕셔너리 값 ===")
    
    try:
        # 한글 데이터가 포함되어 있을 가능성이 높으므로 encoding='utf-8'을 지정합니다.
        with open(filename, 'r', encoding='utf-8') as file:
            data = json.load(file)
            
            # 1. JSON 구조가 리스트(배열) 형태인 경우: [ {"a": 1, "b": 2}, {...} ]
            if isinstance(data, list) and len(data) > 0:
                first_dict = data[0]
                
                if isinstance(first_dict, dict):
                    # .values()를 사용하여 딕셔너리의 값들만 추출
                    print(list(first_dict.values()))
                else:
                    print("리스트의 첫 번째 요소가 딕셔너리가 아닙니다.")
                    
            # 2. JSON 구조가 딕셔너리(객체) 형태인 경우: { "item1": 1, "item2": 2 }
            elif isinstance(data, dict) and len(data) > 0:
                # 최상단 딕셔너리 안의 '값'들만 모두 출력하려면:
                print(list(data.values())[0]) 
                # (만약 최상단 딕셔너리 전체의 값들을 보고 싶다면 list(data.values()) 를 사용하세요.)
                
            else:
                print("데이터가 비어있거나 예상한 형식이 아닙니다.")
                
    except FileNotFoundError:
        print(f"오류: '{filename}' 파일을 찾을 수 없습니다. 파일 경로를 확인해주세요.")
    except json.JSONDecodeError:
        print(f"오류: '{filename}' 파일이 올바른 JSON 형식이 아닙니다.")