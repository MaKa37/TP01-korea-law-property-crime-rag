import json
import os

# ==========================================
# 1. 경로 및 대상 파일 설정
# ==========================================
# 첨부된 이미지 구조에 맞춘 폴더명
BASE_DIR = "Chunk"
# 샘플 파일을 저장할 하위 폴더명
SAMPLE_DIR = os.path.join(BASE_DIR, "samples")

# 추출할 타겟 파일 목록 (실제 청크 데이터가 들어있는 refined 위주)
TARGET_FILES = [
    "chunked_DB01_law_parsed.json",
    "chunked_DB01_law_refined.json",
    "chunked_DB03_expc_parsed.json",
    "chunked_DB03_expc_refined.json",
    "chunked_DB19_prec_parsed.json",
    "chunked_DB19_prec_refined.json"
]

# ==========================================
# 2. 샘플 추출 및 저장 로직
# ==========================================
def extract_samples():
    # 샘플 저장 폴더가 없으면 생성
    if not os.path.exists(SAMPLE_DIR):
        os.makedirs(SAMPLE_DIR)
        print(f"📁 샘플 저장 폴더 생성 완료: {SAMPLE_DIR}")

    for filename in TARGET_FILES:
        filepath = os.path.join(BASE_DIR, filename)
        
        # 파일 존재 여부 확인
        if not os.path.exists(filepath):
            print(f"[경고] 파일을 찾을 수 없습니다: {filepath}")
            continue
            
        try:
            # 원본 JSON 읽기
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 데이터가 리스트 형태인지 확인 후 첫 3개 슬라이싱
            if isinstance(data, list):
                sample_data = data[:3]
            else:
                print(f"[경고] {filename} 내 데이터가 리스트 형식이 아닙니다.")
                continue
            
            # 샘플 데이터를 새로운 JSON 파일로 저장
            sample_filename = f"sample_{filename}"
            sample_filepath = os.path.join(SAMPLE_DIR, sample_filename)
            
            with open(sample_filepath, 'w', encoding='utf-8') as out_f:
                json.dump(sample_data, out_f, ensure_ascii=False, indent=2)
                
            print(f"✅ [추출 완료] {filename} ➡️ {sample_filename} (추출 개수: {len(sample_data)}개)")
            
        except json.JSONDecodeError:
            print(f"[오류] JSON 형식이 잘못되었습니다: {filepath}")
        except Exception as e:
            print(f"[오류] {filename} 처리 중 문제 발생: {e}")

if __name__ == "__main__":
    print("샘플 데이터 추출을 시작합니다...\n")
    extract_samples()
    print("\n모든 작업이 완료되었습니다. Chunk/samples 폴더를 확인해주세요.")