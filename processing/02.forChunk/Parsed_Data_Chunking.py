import json
import re

# ---------------------------------------------------------
# 1. 공통 텍스트 정제 헬퍼 함수
# ---------------------------------------------------------
def clean_text(text):
    if not text:
        return ""
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    return text.strip()

def ensure_list(data):
    if isinstance(data, dict):
        return [data]
    elif isinstance(data, list):
        return data
    return []

def get_string(data):
    """리스트 형태의 텍스트가 들어와도 안전하게 문자열로 결합 (TypeError 방지)"""
    if not data:
        return ""
    if isinstance(data, list):
        return "\n".join(str(item) for item in data)
    return str(data)

# ---------------------------------------------------------
# 2. 데이터별 청킹(Chunking) 파서 로직
# ---------------------------------------------------------
def chunk_law_data(law_json_list):
    """DB01 법령 데이터 청킹 (리스트 내 딕셔너리 구조 반영)"""
    chunks = []
    
    for item in law_json_list:
        # 1. 메타데이터 추출 (최상단 키 또는 본문 내부 키 모두 호환되도록 처리)
        law_name = item.get("법령명한글") or item.get("본문", {}).get("법령", {}).get("기본정보", {}).get("법령명_한글", "알수없는법령")
        law_id = item.get("법령ID") or item.get("본문", {}).get("법령", {}).get("기본정보", {}).get("법령ID", "")
        
        # 2. 본문 조문 추출
        law_info = item.get("본문", {}).get("법령", {})
        articles = ensure_list(law_info.get("조문", {}).get("조문단위", []))
        
        # 본문(조문)이 없는 parsed 데이터일 경우 청킹 건너뛰기
        if not articles:
            continue
            
        for article in articles:
            article_no = str(article.get("조문번호", ""))
            title = get_string(article.get("조문제목", ""))
            content = get_string(article.get("조문내용", ""))
            
            text_parts = [f"[{law_name}]"]
            if title:
                text_parts.append(f"제{article_no}조({title})")
            if content:
                text_parts.append(content)
            
            # '항' 파싱
            hangs = ensure_list(article.get("항", []))
            for hang in hangs:
                hang_content = get_string(hang.get("항내용"))
                if hang_content:
                    text_parts.append(hang_content)
                
                # '항' 하위의 '호' 파싱
                hos = ensure_list(hang.get("호", []))
                for ho in hos:
                    ho_content = get_string(ho.get("호내용"))
                    if ho_content:
                        text_parts.append(ho_content)
            
            # '조문' 직속의 '호' 파싱
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
    """DB03 법령해석례 데이터 청킹"""
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
            f"[질의요지] {clean_text(question)}",
            f"[회답] {clean_text(answer)}",
            f"[이유] {clean_text(reason)}"
        ]
        
        chunks.append({
            "metadata": {"doc_type": "expc", "id": item.get("법령해석례일련번호", "")},
            "chunk_text": "\n".join(text_parts)
        })
        
    return chunks

def chunk_prec_data(prec_json_list):
    """DB19 판례 데이터 청킹"""
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
            "chunk_text": "\n".join(text_parts)
        })
        
    return chunks

# ---------------------------------------------------------
# 3. 테스트 실행 및 파일 저장
# ---------------------------------------------------------
if __name__ == "__main__":
    # JSON 파일 로드
    try:
        with open('Json_Files/DB01_law_refined.json', 'r', encoding='utf-8') as f:
            law_data = json.load(f)
        with open('Json_Files/DB03_expc_refined.json', 'r', encoding='utf-8') as f:
            expc_data = json.load(f)
        with open('Json_Files/DB19_prec_refined.json', 'r', encoding='utf-8') as f:
            prec_data = json.load(f)
    except FileNotFoundError as e:
        print(f"파일을 찾을 수 없습니다: {e}")
        law_data, expc_data, prec_data = [], [], []

    # 청킹 실행
    law_chunks = chunk_law_data(law_data)
    expc_chunks = chunk_expc_data(expc_data)
    prec_chunks = chunk_prec_data(prec_data)
    
    # JSON 파일로 저장
    def save_chunks_to_json(chunks, filename):
        if not chunks:
            print(f"저장할 데이터가 없습니다 (본문 내용이 없는 파일일 수 있습니다): {filename}")
            return
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(chunks, f, ensure_ascii=False, indent=2)
        print(f"[저장 완료] {filename} (총 {len(chunks)}개 청크)")

    save_chunks_to_json(law_chunks, 'chunked_DB01_law.json')
    save_chunks_to_json(expc_chunks, 'chunked_DB03_expc.json')
    save_chunks_to_json(prec_chunks, 'chunked_DB19_prec.json')