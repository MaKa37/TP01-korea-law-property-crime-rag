import json
from pathlib import Path

# ===== 설정 =====
PARSED_PATH = Path("Json_Files/DB01_law_parsed_ALL.json")   # 현행연혁코드 등 메타용
REFINED_PATH = Path("Json_Files/DB01_law_refined.json")     # 본문(조문) 데이터
OUTPUT_PATH = Path("Json_Files/DB01_law_chunks.json")

# 조문내용에 이 키워드가 있으면 '삭제된 조문'으로 간주하고 청크 생성 제외
DELETED_KEYWORDS = ["삭제"]


def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def ensure_list(value):
    """단일 dict 또는 list를 항상 list로 통일"""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def build_parsed_lookup(parsed_data):
    """법령일련번호 -> 현행연혁코드 등 메타 매핑 생성"""
    lookup = {}
    for item in parsed_data:
        mst = item.get("법령일련번호")
        if mst:
            lookup[mst] = item
    return lookup


def is_deleted_article(article_content: str) -> bool:
    return any(keyword in article_content for keyword in DELETED_KEYWORDS)


def build_article_text(article: dict) -> str:
    """조문내용 + 항/호 내용을 하나의 텍스트로 결합"""
    parts = []

    intro = article.get("조문내용", "").strip()
    if intro:
        parts.append(intro)

    for hang in ensure_list(article.get("항")):
        hang_content = hang.get("항내용", "").strip()
        if hang_content:
            parts.append(hang_content)

        for ho in ensure_list(hang.get("호")):
            ho_content = ho.get("호내용", "").strip()
            if ho_content:
                parts.append(ho_content)

    # 항 없이 조문에 바로 호가 붙는 경우 (예: 제2조 정의)
    for ho in ensure_list(article.get("호")):
        ho_content = ho.get("호내용", "").strip()
        if ho_content:
            parts.append(ho_content)

    return "\n".join(parts)


def build_chunk_id(mst: str, article: dict) -> str:
    jo_num = str(article.get("조문번호", "0")).zfill(4)
    branch_num = article.get("조문가지번호")
    if branch_num:
        return f"{mst}_{jo_num}_{str(branch_num).zfill(2)}"
    return f"{mst}_{jo_num}"


def extract_articles(law_body: dict):
    """본문(법령) JSON에서 조문단위 리스트 추출"""
    try:
        articles = law_body["법령"]["조문"]["조문단위"]
    except (KeyError, TypeError):
        return []
    return ensure_list(articles)


def process_entry(entry: dict, parsed_lookup: dict):
    """refined 항목 하나(법령 하나)를 조문 청크 리스트로 변환"""
    mst = entry.get("법령일련번호")
    body = entry.get("본문", {})

    basic_info = {}
    try:
        basic_info = body["법령"]["기본정보"]
    except (KeyError, TypeError):
        pass

    parsed_meta = parsed_lookup.get(mst, {})

    law_name = basic_info.get("법령명_한글") or parsed_meta.get("법령명한글", "")
    law_short_name = basic_info.get("법령명약칭") or parsed_meta.get("법령약칭명", "")
    effective_date = basic_info.get("시행일자") or parsed_meta.get("시행일자", "")
    law_type = basic_info.get("법종구분", {}).get("content") or parsed_meta.get("법령구분명", "")
    dept_name = basic_info.get("소관부처", {}).get("content") or parsed_meta.get("소관부처명", "")
    status_code = parsed_meta.get("현행연혁코드", "")  # refined 본문에는 없음, parsed에서만 확인 가능

    chunks = []
    articles = extract_articles(body)

    for article in articles:
        # 장/절 구분 등 실제 조문이 아닌 것은 제외
        if article.get("조문여부") != "조문":
            continue

        article_text = build_article_text(article)

        if is_deleted_article(article_text):
            continue  # 삭제된 조문은 청크 생성하지 않음

        if not article_text.strip():
            continue  # 내용 없는 조문 스킵

        chunk = {
            "chunk_id": build_chunk_id(mst, article),
            "법령일련번호": mst,
            "법령명": law_name,
            "법령약칭명": law_short_name,
            "조문번호": article.get("조문번호", ""),
            "조문가지번호": article.get("조문가지번호", ""),
            "조문제목": article.get("조문제목", ""),
            "조문내용": article_text,
            "시행일자": effective_date,
            "현행연혁코드": status_code,
            "법령구분명": law_type,
            "소관부처명": dept_name,
        }
        chunks.append(chunk)

    return chunks


def main():
    parsed_data = load_json(PARSED_PATH)
    refined_data = load_json(REFINED_PATH)

    if isinstance(parsed_data, dict):
        for key in ("law", "items", "data"):
            if key in parsed_data and isinstance(parsed_data[key], list):
                parsed_data = parsed_data[key]
                break

    if isinstance(refined_data, dict):
        for key in ("law", "items", "data"):
            if key in refined_data and isinstance(refined_data[key], list):
                refined_data = refined_data[key]
                break

    parsed_lookup = build_parsed_lookup(parsed_data)

    all_chunks = []
    skipped_laws = 0

    for entry in refined_data:
        chunks = process_entry(entry, parsed_lookup)
        if not chunks:
            skipped_laws += 1
        all_chunks.extend(chunks)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, ensure_ascii=False, indent=2)

    print(f"법령 수: {len(refined_data)}건")
    print(f"청크(조문) 생성 수: {len(all_chunks)}건")
    print(f"청크가 하나도 안 만들어진 법령: {skipped_laws}건")
    print(f"✅ 저장됨 -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()