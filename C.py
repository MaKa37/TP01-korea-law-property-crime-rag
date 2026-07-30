import json
import os
import re
from typing import Any

# 경로 설정
INPUT_DIR = "./Json_Files"
OUTPUT_DIR = "./Chunk"

os.makedirs(OUTPUT_DIR, exist_ok=True)


def normalize_text(value: Any) -> str:
    """
    문자열의 불필요한 공백과 줄바꿈을 정리한다.
    문자열이 아니거나 값이 없으면 빈 문자열을 반환한다.
    """
    if value is None:
        return ""

    if not isinstance(value, str):
        value = str(value)

    value = value.replace("\u00a0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n\s*\n+", "\n", value)

    return value.strip()


def ensure_list(value: Any) -> list:
    """
    국가법령정보 API에서는 데이터가 1개일 때 dict,
    여러 개일 때 list로 반환되는 경우가 있으므로 항상 list로 변환한다.
    """
    if value is None:
        return []

    if isinstance(value, list):
        return value

    if isinstance(value, dict):
        return [value]

    return []


def append_unique_text(texts: list[str], value: Any) -> None:
    """
    빈 문자열과 중복 문자열을 제외하고 texts에 추가한다.
    """
    text = normalize_text(value)

    if text and text not in texts:
        texts.append(text)


def extract_mok_text(mok_data: Any) -> list[str]:
    """
    목 데이터를 추출한다.

    예상 구조:
    {
        "목번호": "가.",
        "목내용": "가. ..."
    }
    또는 위 객체의 리스트
    """
    texts = []

    for mok in ensure_list(mok_data):
        if not isinstance(mok, dict):
            continue

        append_unique_text(texts, mok.get("목내용"))

    return texts


def extract_ho_text(ho_data: Any) -> list[str]:
    """
    호와 그 하위 목 데이터를 추출한다.
    """
    texts = []

    for ho in ensure_list(ho_data):
        if not isinstance(ho, dict):
            continue

        append_unique_text(texts, ho.get("호내용"))

        # 일부 데이터는 "목", 일부는 다른 래퍼 구조를 가질 수 있음
        mok_data = ho.get("목")

        if isinstance(mok_data, dict) and "목단위" in mok_data:
            mok_data = mok_data.get("목단위")

        for mok_text in extract_mok_text(mok_data):
            append_unique_text(texts, mok_text)

    return texts


def extract_hang_text(hang_data: Any) -> list[str]:
    """
    항과 그 하위 호·목 데이터를 추출한다.

    첨부 데이터에서는 다음 두 형태가 모두 존재한다.

    1. "항": [{...}, {...}]
    2. "항": {"호": [{...}, {...}]}
    """
    texts = []

    for hang in ensure_list(hang_data):
        if not isinstance(hang, dict):
            continue

        append_unique_text(texts, hang.get("항내용"))

        ho_data = hang.get("호")

        if isinstance(ho_data, dict) and "호단위" in ho_data:
            ho_data = ho_data.get("호단위")

        for ho_text in extract_ho_text(ho_data):
            append_unique_text(texts, ho_text)

    return texts


def build_article_number(jo: dict) -> str:
    """
    조문번호와 조문가지번호를 결합한다.

    예:
    조문번호=3, 조문가지번호=2 -> 3의2
    """
    jo_num = normalize_text(jo.get("조문번호"))
    branch_num = normalize_text(jo.get("조문가지번호"))

    if jo_num and branch_num:
        return f"{jo_num}의{branch_num}"

    return jo_num


def build_article_text(jo: dict) -> str:
    """
    하나의 조문에서 조문내용, 항, 호, 목을 모두 추출한다.
    """
    texts = []

    # 조문 본문
    append_unique_text(texts, jo.get("조문내용"))

    # 항 → 호 → 목
    for hang_text in extract_hang_text(jo.get("항")):
        append_unique_text(texts, hang_text)

    # 데이터에 따라 호가 조문 바로 아래 존재할 가능성도 처리
    for ho_text in extract_ho_text(jo.get("호")):
        append_unique_text(texts, ho_text)

    # 데이터에 따라 목이 조문 바로 아래 존재할 가능성도 처리
    for mok_text in extract_mok_text(jo.get("목")):
        append_unique_text(texts, mok_text)

    return "\n".join(texts).strip()


def make_article_header(
    law_name: str,
    article_num: str,
    article_title: str
) -> str:
    """
    청크 앞에 들어갈 조문 헤더를 생성한다.
    """
    law_label = f"[{law_name}]" if law_name else "[법령명 없음]"

    if article_num:
        article_label = f"제{article_num}조"
    else:
        article_label = "조문번호 없음"

    if article_title:
        article_label += f"({article_title})"

    return f"{law_label} {article_label}"


def chunk_db01_law(file_path: str) -> list[dict]:
    """
    DB01 법령 데이터를 조문 단위로 청킹한다.

    조문내용뿐 아니라 항, 호, 목까지 포함한다.
    """
    with open(file_path, "r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, list):
        raise ValueError(
            f"최상위 JSON 데이터는 list여야 합니다. "
            f"현재 타입: {type(data).__name__}"
        )

    chunks = []

    for item_index, item in enumerate(data):
        if not isinstance(item, dict):
            print(
                f"[WARN] item_index={item_index}: "
                f"dict가 아니므로 건너뜁니다."
            )
            continue

        law_data = (
            item.get("본문", {})
            .get("법령", {})
        )

        if not isinstance(law_data, dict):
            print(
                f"[WARN] item_index={item_index}: "
                f"'본문.법령' 데이터가 올바르지 않습니다."
            )
            continue

        base_info = law_data.get("기본정보", {})
        if not isinstance(base_info, dict):
            base_info = {}

        law_name = normalize_text(base_info.get("법령명_한글"))
        law_id = normalize_text(base_info.get("법령ID"))
        enforcement_date = normalize_text(base_info.get("시행일자"))
        proclamation_date = normalize_text(base_info.get("공포일자"))
        proclamation_number = normalize_text(base_info.get("공포번호"))

        jo_data = law_data.get("조문", {})
        if not isinstance(jo_data, dict):
            print(
                f"[WARN] {law_name or item_index}: "
                f"'조문' 데이터가 없거나 dict가 아닙니다."
            )
            continue

        jo_list = ensure_list(jo_data.get("조문단위"))

        for jo_index, jo in enumerate(jo_list):
            if not isinstance(jo, dict):
                print(
                    f"[WARN] {law_name} jo_index={jo_index}: "
                    f"조문 데이터가 dict가 아닙니다."
                )
                continue

            # 실제 조문만 저장하고 싶은 경우 사용
            jo_type = normalize_text(jo.get("조문여부"))
            if jo_type and jo_type != "조문":
                continue

            article_num = build_article_number(jo)
            article_title = normalize_text(jo.get("조문제목"))
            article_text = build_article_text(jo)

            if not article_text:
                print(
                    f"[WARN] 본문이 비어 있어 건너뜁니다: "
                    f"{law_name} 제{article_num}조"
                )
                continue

            article_key = normalize_text(jo.get("조문키"))

            chunk_id_parts = [
                "DB01",
                law_id or normalize_text(item.get("법령일련번호")),
                article_key or article_num,
            ]

            chunk_id = "_".join(
                part.replace(" ", "_")
                for part in chunk_id_parts
                if part
            )

            metadata = {
                "chunk_id": chunk_id,
                "source": "DB01",
                "document_type": "law_article",
                "law_serial_number": normalize_text(
                    item.get("법령일련번호")
                ),
                "law_id": law_id,
                "law_name": law_name,
                "article_key": article_key,
                "article_num": article_num,
                "article_title": article_title,
                "article_effective_date": normalize_text(
                    jo.get("조문시행일자")
                ),
                "article_changed": normalize_text(
                    jo.get("조문변경여부")
                ),
                "enforcement_date": enforcement_date,
                "proclamation_date": proclamation_date,
                "proclamation_number": proclamation_number,
                "section": "조문",
                "chunk_index": 0,
            }

            header = make_article_header(
                law_name=law_name,
                article_num=article_num,
                article_title=article_title,
            )

            page_content = f"{header}\n{article_text}".strip()

            chunks.append(
                {
                    "page_content": page_content,
                    "metadata": metadata,
                }
            )

    return chunks


def save_chunks(chunks: list[dict], output_filename: str) -> None:
    """
    생성한 청크를 JSON 파일로 저장한다.
    """
    output_path = os.path.join(OUTPUT_DIR, output_filename)

    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(
            chunks,
            file,
            ensure_ascii=False,
            indent=2,
        )

    print(f"Saved {len(chunks)} DB01 chunks to {output_path}")


def main() -> None:
    input_file = os.path.join(
        INPUT_DIR,
        "DB01_law_parsed.json",
    )

    if not os.path.isfile(input_file):
        print(f"File not found: {input_file}")
        return

    try:
        law_chunks = chunk_db01_law(input_file)

        save_chunks(
            law_chunks,
            "DB01_law_chunked.json",
        )

        if law_chunks:
            print("\n첫 번째 청크 미리보기")
            print("-" * 60)
            print(law_chunks[0]["page_content"])
            print("-" * 60)
            print(
                json.dumps(
                    law_chunks[0]["metadata"],
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print("[WARN] 생성된 청크가 없습니다.")

    except json.JSONDecodeError as error:
        print(
            f"JSON 형식 오류: "
            f"line={error.lineno}, "
            f"column={error.colno}, "
            f"message={error.msg}"
        )

    except OSError as error:
        print(f"파일 처리 오류: {error}")

    except Exception as error:
        print(
            f"예상하지 못한 오류가 발생했습니다: "
            f"{type(error).__name__}: {error}"
        )


if __name__ == "__main__":
    main()