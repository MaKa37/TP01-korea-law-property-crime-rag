import json
import re
import os

# ==========================================
# 1. 공통 데이터 정제 및 헬퍼 함수
# ==========================================
def clean_text(text):
    """HTML 태그 제거 및 줄바꿈 정리 (판례 데이터 등에서 활용)"""
    if not text:
        return ""
    # <br> 태그류는 의미적 줄바꿈을 위해 \n으로 치환
    text = re.sub(r'<br\s*/?>', '\n', text)
    # 나머지 모든 HTML 태그 삭제
    text = re.sub(r'<[^>]+>', '', text)
    return text.strip()

def ensure_list(data):
    """딕셔너리나 단일 객체가 들어와도 안전하게 리스트로 변환"""
    if isinstance(data, dict):
        return [data]
    elif isinstance(data, list):
        return data
    return []

def get_string(data):
    """리스트 구조가 섞여 들어오는 에러(TypeError)를 방지하고 문자열로 결합"""
    if not data:
        return ""
    if isinstance(data, list):
        return "\n".join(str(item) for item in data)
    return str(data)

# ==========================================
# 2. 데이터 도메인별 청킹(Chunking) 로직
# ==========================================
def chunk_law_data(law_json_list):
    """[법령] 컨텍스트 크기를 최대화하여 조문단위(조, 항, 호)를 하나의 청크로 병합"""
    chunks = []
    
    for item in law_json_list:
        law_info = item.get("본문", {}).get("법령", {})
        if not law_info:
            continue
            
        base_info = law_info.get("기본정보", {})
        law_name = get_string(base_info.get("법령명_한글", "알수없는법령"))
        law_id = get_string(base_info.get("법령ID", ""))
        
        articles = ensure_list(law_info.get("조문", {}).get("조문단위", []))
        
        for article in articles:
            article_no = get_string(article.get("조문번호", ""))
            title = get_string(article.get("조문제목", ""))
            content = get_string(article.get("조문내용", ""))
            
            text_parts = [f"[{law_name}]"]
            if title:
                text_parts.append(f"제{article_no}조({title})")
            if content:
                text_parts.append(content)
            
            # '항' 단위 추출 및 병합
            hangs = ensure_list(article.get("항", []))
            for hang in hangs:
                hang_content = get_string(hang.get("항내용"))
                if hang_content:
                    text_parts.append(hang_content)
                
                # '항' 밑에 속한 '호' 단위 추출 및 병합
                hos = ensure_list(hang.get("호", []))
                for ho in hos:
                    ho_content = get_string(ho.get("호내용"))
                    if ho_content:
                        text_parts.append(ho_content)
            
            # '조문' 직속 '호' 단위 추출 및 병합
            direct_hos = ensure_list(article.get("호", []))
            for ho in direct_hos:
                ho_content = get_string(ho.get("호내용"))
                if ho_content:
                    text_parts.append(ho_content)
                    
            chunks.append({
                "metadata": {"doc_type": "law", "law_id": law_id, "law_name": law_name, "article_no": article_no},
                "chunk_text": "\n".join(text_parts)
            })
            
    return chunks

def chunk_expc_data(expc_json_list):
    """[법령해석례] 질의, 회답, 이유를 모두 병합하여 온전한 사실관계 컨텍스트 제공"""
    chunks = []
    
    for item in expc_json_list:
        body = item.get("본문", {}).get("ExpcService", {})
        if not body:
            continue
            
        title = get_string(body.get("안건명", ""))
        question = get_string(body.get("질의요지", ""))
        answer = get_string(body.get("회답", ""))
        reason = get_string(body.get("이유", ""))
        
        text_parts = [
            f"[안건명] {clean_text(title)}",
            f"[질의요지]\n{clean_text(question)}",
            f"[회답]\n{clean_text(answer)}",
            f"[이유]\n{clean_text(reason)}"
        ]
        
        chunks.append({
            "metadata": {"doc_type": "expc", "id": get_string(item.get("법령해석례일련번호", ""))},
            "chunk_text": "\n\n".join(text_parts)
        })
        
    return chunks

def chunk_prec_data(prec_json_list):
    """[판례] 판시사항, 판결요지, 참조조문을 묶어 하나의 판례 컨텍스트로 구성"""
    chunks = []
    
    for item in prec_json_list:
        body = item.get("본문", {}).get("PrecService", {})
        if not body:
            continue
            
        case_name = get_string(body.get("사건명", ""))
        case_no = get_string(body.get("사건번호", ""))
        
        summary = clean_text(get_string(body.get("판시사항", "")))
        gist = clean_text(get_string(body.get("판결요지", "")))
        ref_law = clean_text(get_string(body.get("참조조문", "")))
        
        text_parts = [
            f"[사건명] {case_name}",
            f"[사건번호] {case_no}",
            f"[판시사항]\n{summary}",
            f"[판결요지]\n{gist}",
            f"[참조조문]\n{ref_law}"
        ]
        
        chunks.append({
            "metadata": {"doc_type": "prec", "case_no": case_no},
            "chunk_text": "\n\n".join(text_parts)
        })
        
    return chunks

# ==========================================
# 3. 파이프라인 실행부
# ==========================================
if __name__ == "__main__":
    # 작업 디렉터리 설정 (첨부된 이미지의 구조 반영)
    BASE_DIR = "Json_Files"
    
    # 1. 파일 경로 지정
    paths = {
        "law": os.path.join(BASE_DIR, "DB01_law_refined.json"),
        "expc": os.path.join(BASE_DIR, "DB03_expc_refined.json"),
        "prec": os.path.join(BASE_DIR, "DB19_prec_refined.json")
    }
    
    # 2. JSON 데이터 안전하게 로드하는 헬퍼 함수
    def load_json(filepath):
        if not os.path.exists(filepath):
            print(f"[경고] 파일을 찾을 수 없습니다: {filepath}")
            return []
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"[오류] JSON 형식이 잘못되었습니다: {filepath}")
            return []

    # 데이터 로드
    print("데이터를 불러오는 중입니다...")
    law_data = load_json(paths["law"])
    expc_data = load_json(paths["expc"])
    prec_data = load_json(paths["prec"])

    # 3. 청킹 실행
    print("데이터 청킹 작업을 시작합니다...")
    law_chunks = chunk_law_data(law_data)
    expc_chunks = chunk_expc_data(expc_data)
    prec_chunks = chunk_prec_data(prec_data)
    
    # 4. JSON 파일로 결과물 저장
    def save_chunks(chunks, filename):
        if not chunks:
            print(f"[알림] 저장할 데이터가 없습니다: {filename}")
            return
            
        filepath = os.path.join(BASE_DIR, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(chunks, f, ensure_ascii=False, indent=2)
        print(f"✅ [저장 완료] {filepath} (총 {len(chunks)}개 청크 생성)")

    # 결과물 저장 (Json_Files 디렉터리 내부)
    save_chunks(law_chunks, 'chunked_DB01_law.json')
    save_chunks(expc_chunks, 'chunked_DB03_expc.json')
    save_chunks(prec_chunks, 'chunked_DB19_prec.json')