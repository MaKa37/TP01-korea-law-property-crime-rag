#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Legal RAG 청킹 파이프라인 (고도화 버전)
=========================
DB01(법령) / DB03(법령해석례) / DB19(판례) 의 parsed(목록)+refined(본문) 원본 쌍을
1) 중복 없이 병합하고
2) 도메인별로 의미 단위 섹션으로 분할하고
3) rag_chunks 테이블 스키마에 바로 적재 가능한 형태로 출력한다.

[개선 및 반영 사항]
 ① parsed/refined 데이터 조인(Join) 병합 완료.
 ② prec(판례)의 【주문】, 【이유】 등 꺾쇠 헤더 추출 시 정규표현식 개선 (공백 완벽 제거 및 바디 텍스트 분리).
 ③ law_sn/expc_sn/prec_sn 등 식별자 및 메타데이터 복원.
 ④ 긴 본문을 ○ 불릿 및 최상위 목차(1. 2. 3.) 기준으로 컨텍스트 손실 없이 분할.
 ⑤ 텍스트 필드가 리스트(list)로 들어올 경우 TypeError 방지를 위한 get_string 적용.
"""

import json
import re
import argparse
from pathlib import Path

# ----------------------------------------------------------------------
# 공통 유틸리티
# ----------------------------------------------------------------------

def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def clean_html(text):
    """<br/> 등 HTML 잔재 제거 + 공백 정리. 표시용이 아닌 임베딩용 정제 텍스트 생성."""
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)          # 남은 HTML 태그 삭제
    text = re.sub(r"[ \t]+", " ", text)          # 과도한 탭/공백 압축
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)  # 과도한 빈줄 정리
    return text.strip()

def normalize_date(s):
    """'2026.05.14' / '20260514' / '2026-05-14' -> '2026-05-14' (YYYY-MM-DD)."""
    if not s:
        return None
    digits = re.sub(r"\D", "", s)
    if len(digits) != 8:
        return None
    return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"

def get_string(data):
    """리스트 구조가 섞여 들어오는 에러 방지 및 문자열 결합."""
    if data is None:
        return ""
    if isinstance(data, list):
        return "\n".join(str(item) for item in data)
    return str(data)

def ensure_list(data):
    """단일 dict가 들어와도 안전하게 list로 변환."""
    if isinstance(data, dict):
        return [data]
    elif isinstance(data, list):
        return data
    return []

def index_by(records, key):
    """리스트를 key 필드 기준 dict로 변환 (parsed/refined 병합 조인용)."""
    out = {}
    for r in records:
        k = r.get(key)
        if k is not None:
            out[str(k)] = r
    return out

def make_chunk(source_type, source_sn, section_type, seq_no, title, chunk_text, extra_meta):
    """rag_chunks 테이블 적재용 표준 딕셔너리 포맷 생성."""
    meta = {
        "doc_type": source_type,
        "source_sn": str(source_sn),
        "section_type": section_type,
        "seq_no": seq_no,
    }
    meta.update(extra_meta)
    return {
        "chunk_id": f"{source_type}:{source_sn}:{section_type}:{seq_no}",
        "metadata": meta,
        "chunk_text": chunk_text.strip(),
    }

def split_long_text(text, marker="○", max_len=1200):
    """긴 본문을 불릿(○) 경계 기준으로 max_len 근처까지 뭉쳐서 분할."""
    text = text.strip()
    if len(text) <= max_len:
        return [text]
    parts = [p for p in re.split(rf"(?={re.escape(marker)})", text) if p.strip()]
    if len(parts) <= 1:
        return [text]
    
    merged, buf = [], ""
    for p in parts:
        if not buf or len(buf) + len(p) <= max_len:
            buf += p
        else:
            merged.append(buf.strip())
            buf = p
    if buf.strip():
        merged.append(buf.strip())
    return merged

# ----------------------------------------------------------------------
# 1) 법령 (DB01_law) - 조문 단위 청킹
# ----------------------------------------------------------------------

def _extract_texts(node):
    """항/호/목이 dict 또는 list로 뒤섞여 있어도 안전하게 내용 텍스트를 순서대로 수집."""
    texts = []
    if not node:
        return texts
    if isinstance(node, str):
        return [node.strip()]
    if isinstance(node, list):
        for item in node:
            texts.extend(_extract_texts(item))
        return texts
    if isinstance(node, dict):
        for key in ("항내용", "호내용", "목내용"):
            val = node.get(key)
            if val:
                texts.append(get_string(val).strip())
        for subkey in ("항", "호", "목"):
            if subkey in node:
                texts.extend(_extract_texts(node[subkey]))
        return texts
    return texts

def chunk_law(parsed_records, refined_records):
    parsed_idx = index_by(parsed_records, "법령일련번호")
    chunks = []
    for rec in refined_records:
        law_sn = str(rec.get("법령일련번호", ""))
        p = parsed_idx.get(law_sn, {})
        try:
            body = rec["본문"]["법령"]
        except (KeyError, TypeError):
            continue

        info = body.get("기본정보", {})
        law_name = get_string(info.get("법령명_한글") or p.get("법령명한글"))
        law_id = get_string(info.get("법령ID") or p.get("법령ID"))
        law_type = get_string((info.get("법종구분") or {}).get("content") or p.get("법령구분명"))
        ministry = get_string((info.get("소관부처") or {}).get("content") or p.get("소관부처명"))

        common_meta = {
            "law_sn": law_sn,
            "law_id": law_id,
            "law_name": law_name,
            "law_abbr": get_string(info.get("법령명약칭") or p.get("법령약칭명")),
            "law_type": law_type,
            "status": get_string(p.get("현행연혁코드")),
            "amend_type": get_string(info.get("제개정구분") or p.get("제개정구분명")),
            "ministry_name": ministry,
            "announce_date": normalize_date(info.get("공포일자") or p.get("공포일자")),
            "enforce_date": normalize_date(info.get("시행일자") or p.get("시행일자")),
        }

        articles = ensure_list((body.get("조문") or {}).get("조문단위", []))
        seq = 0
        for art in articles:
            if art.get("조문여부") != "조문":
                continue 
            
            art_no = get_string(art.get("조문번호"))
            art_sub = get_string(art.get("조문가지번호"))
            art_title = get_string(art.get("조문제목"))
            art_key = get_string(art.get("조문키"))
            art_enforce = normalize_date(art.get("조문시행일자"))
            content = get_string(art.get("조문내용")).strip()
            
            body_lines = [content] if content else []
            body_lines.extend(_extract_texts(art.get("항")))
            body_lines.extend(_extract_texts(art.get("호"))) # 조문 직속 호 처리
            
            article_text = "\n".join([l for l in body_lines if l])
            if not article_text:
                continue
                
            is_deleted = "삭제" in content and len(article_text) < 40

            # 조문가지번호(의2, 의3) 반영 로직 보완
            display_no = f"제{art_no}조"
            if art_sub and art_sub != "0":
                display_no += f"의{art_sub}"
                
            title = f"{law_name} {display_no}" + (f"({art_title})" if art_title else "")
            header = f"[{law_name}]\n{display_no}" + (f"({art_title})" if art_title else "")
            full_text = f"{header}\n{article_text}"

            meta = dict(common_meta)
            meta.update({
                "article_key": art_key,
                "article_no": art_no,
                "article_sub_no": art_sub if art_sub else None,
                "article_title": art_title,
                "article_enforce_date": art_enforce,
                "is_deleted": is_deleted,
            })
            chunks.append(make_chunk("law", law_sn, "조문", seq, title, full_text, meta))
            seq += 1
    return chunks

# ----------------------------------------------------------------------
# 2) 법령해석례 (DB03_expc) - 질의요지/회답/이유 섹션 청킹
# ----------------------------------------------------------------------

def chunk_expc(parsed_records, refined_records):
    parsed_idx = index_by(parsed_records, "법령해석례일련번호")
    chunks = []
    for rec in refined_records:
        expc_sn = str(rec.get("법령해석례일련번호", ""))
        p = parsed_idx.get(expc_sn, {})
        try:
            body = rec["본문"]["ExpcService"]
        except (KeyError, TypeError):
            continue

        case_name = get_string(body.get("안건명") or p.get("안건명"))
        common_meta = {
            "expc_sn": expc_sn,
            "case_no": get_string(body.get("안건번호") or p.get("안건번호")),
            "case_name": case_name,
            "request_org_name": get_string(body.get("질의기관명") or p.get("질의기관명")),
            "reply_org_name": get_string(body.get("해석기관명") or p.get("회신기관명")),
            "reply_date": normalize_date(body.get("해석일자") or p.get("회신일자")),
        }
        header = f"[{case_name}]"

        # 질의요지 / 회답: 통상 짧아서 그대로 1청크
        for section_type, field in [("질의요지", "질의요지"), ("회답", "회답")]:
            text = clean_html(get_string(body.get(field)))
            if text:
                full_text = f"{header}\n[{section_type}]\n{text}"
                chunks.append(make_chunk(
                    "expc", expc_sn, section_type, 0,
                    f"{case_name} - {section_type}", full_text, common_meta))

        # 이유: 길면 ○ 불릿 단위로 재분할
        reason = clean_html(get_string(body.get("이유")))
        if reason:
            parts = split_long_text(reason, marker="○", max_len=1200)
            for i, part in enumerate(parts):
                full_text = f"{header}\n[이유{'' if len(parts)==1 else f' {i+1}/{len(parts)}'}]\n{part}"
                chunks.append(make_chunk(
                    "expc", expc_sn, "이유", i,
                    f"{case_name} - 이유 {i+1}/{len(parts)}", full_text, common_meta))
    return chunks

# ----------------------------------------------------------------------
# 3) 판례 (DB19_prec) - 판시사항/판결요지 + 판례내용(【】 섹션) 청킹
# ----------------------------------------------------------------------

def _split_numbered_issues(text):
    """[1] [2] ... 로 번호가 매겨진 쟁점을 분리. 번호가 없으면 전체를 issue '0' 하나로."""
    if not text:
        return {}
    matches = list(re.finditer(r"\[(\d+)\]", text))
    if not matches:
        return {"0": text.strip()}
    out = {}
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out[m.group(1)] = text[start:end].strip()
    return out

def _split_bracket_sections(text):
    """
    판례내용을 【원고,피상고인】 【주문】 【이유】 등 헤더 기준으로 분리.
    정규식을 통해 내부 공백("이    유")을 정규화하고 괄호 이후의 텍스트만 깔끔하게 자른다.
    """
    matches = list(re.finditer(r"【([^】]+)】", text))
    if not matches:
        return [("본문", text.strip())]
    sections = []
    for i, m in enumerate(matches):
        start = m.end()  # 헤더가 끝나는 지점부터 바디 시작
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        header = re.sub(r"\s+", "", m.group(1))  # "이    유" -> "이유"
        sec_body = text[start:end].strip()
        if sec_body:
            sections.append((header, sec_body))
    return sections

# 개행 직후(또는 시작)에 1. 2. 처럼 숫자가 오는 경우만 매칭 (날짜 2026. 05. 와의 혼동 방지)
_TOPLEVEL_ITEM_RE = re.compile(r"(?:^|\n)[ \t]{0,3}(\d{1,2})\.\s+(?=\D)")

def _split_reason_by_toplevel(text, min_len_to_split=1200):
    """【이유】처럼 긴 섹션을 '1. 사안의 개요' '2. 상고이유에 관하여' 등 최상위 목차 기준으로 추가 분할."""
    if len(text) <= min_len_to_split:
        return [text]
    matches = list(_TOPLEVEL_ITEM_RE.finditer(text))
    if not matches:
        return [text]
    parts, start = [], 0
    for m in matches:
        if m.start() > start:
            parts.append(text[start:m.start()].strip())
        start = m.start()
    parts.append(text[start:].strip())
    return [p for p in parts if p]

def chunk_prec(parsed_records, refined_records):
    parsed_idx = index_by(parsed_records, "판례일련번호")
    chunks = []
    for rec in refined_records:
        prec_sn = str(rec.get("판례일련번호", ""))
        p = parsed_idx.get(prec_sn, {})
        try:
            body = rec["본문"]["PrecService"]
        except (KeyError, TypeError):
            continue

        case_name = get_string(body.get("사건명") or p.get("사건명"))
        common_meta = {
            "prec_sn": prec_sn,
            "case_no": get_string(body.get("사건번호") or p.get("사건번호")),
            "case_name": case_name,
            "court_name": get_string(body.get("법원명") or p.get("법원명")),
            "case_type_name": get_string(body.get("사건종류명") or p.get("사건종류명")), 
            "judgment_type": get_string(body.get("판결유형") or p.get("판결유형")),
            "sentence_date": normalize_date(body.get("선고일자") or p.get("선고일자")),
            "ref_articles": clean_html(get_string(body.get("참조조문"))) or None,
            "ref_cases": clean_html(get_string(body.get("참조판례"))) or None,
        }
        header = f"[{case_name}] {common_meta['case_no']}"

        # --- (a) 판시사항 + 판결요지: 쟁점([1][2]..) 단위로 페어링 ---
        headnotes = clean_html(get_string(body.get("판시사항")))
        holdings = clean_html(get_string(body.get("판결요지")))
        hn_map = _split_numbered_issues(headnotes)
        ho_map = _split_numbered_issues(holdings)
        issue_keys = sorted(set(hn_map) | set(ho_map), key=lambda x: int(x))
        
        for i, k in enumerate(issue_keys):
            piece = []
            if hn_map.get(k):
                piece.append(f"[판시사항]\n{hn_map[k]}")
            if ho_map.get(k):
                piece.append(f"[판결요지]\n{ho_map[k]}")
            if not piece:
                continue
            issue_label = f" 쟁점{k}" if k != "0" else ""
            full_text = f"{header}{issue_label}\n" + "\n\n".join(piece)
            chunks.append(make_chunk(
                "prec", prec_sn, "판시사항_판결요지", i,
                f"{case_name} - 쟁점{k}", full_text, common_meta))

        # --- (b) 판례내용: 【】 섹션 단위, 【이유】는 추가로 목차 단위 분할 ---
        content = clean_html(get_string(body.get("판례내용")))
        if content:
            sections = _split_bracket_sections(content)
            seq = 0
            for sec_header, sec_body in sections:
                if sec_header == "이유":
                    sub_parts = _split_reason_by_toplevel(sec_body)
                else:
                    sub_parts = [sec_body]
                    
                for j, sp in enumerate(sub_parts):
                    if not sp:
                        continue
                    label = sec_header + (f" {j+1}/{len(sub_parts)}" if len(sub_parts) > 1 else "")
                    full_text = f"{header}\n[판례내용 - {label}]\n{sp}"
                    chunks.append(make_chunk(
                        "prec", prec_sn, f"판례내용:{sec_header}", seq,
                        f"{case_name} - 판례내용({label})", full_text, common_meta))
                    seq += 1
    return chunks

# ----------------------------------------------------------------------
# Main 실행부
# ----------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Legal RAG 청킹 파이프라인")
    # 경로를 'Json_Files'로 수정하여 원본 파일의 위치를 반영
    ap.add_argument("--input-dir", default="Json_Files", help="원본 parsed/refined json이 있는 디렉토리")
    ap.add_argument("--output-dir", default="./chunks_out", help="청크 출력 디렉토리")
    args = ap.parse_args()

    in_dir = Path(args.input_dir)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 파싱 대상 원본 파일명 매핑
    jobs = [
        ("law", "DB01_law_parsed.json", "DB01_law_refined.json", chunk_law),
        ("expc", "DB03_expc_parsed.json", "DB03_expc_refined.json", chunk_expc),
        ("prec", "DB19_prec_parsed.json", "DB19_prec_refined.json", chunk_prec),
    ]

    all_chunks = []
    for name, parsed_fn, refined_fn, fn in jobs:
        parsed_path = in_dir / parsed_fn
        refined_path = in_dir / refined_fn
        
        if not parsed_path.exists() or not refined_path.exists():
            print(f"[Skip] {name}: {parsed_fn} 또는 {refined_fn} 파일을 찾을 수 없습니다. (경로: {in_dir})")
            continue
            
        parsed = load_json(parsed_path)
        refined = load_json(refined_path)
        result = fn(parsed, refined)
        all_chunks.extend(result)

        out_path = out_dir / f"chunks_{name}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[Success] {name}: {len(result)}개 청크 생성 완료 -> {out_path}")

    # rag_chunks 테이블 적재용 JSONL (DB bulk insert 용이)
    if all_chunks:
        jsonl_path = out_dir / "chunks_all.jsonl"
        with open(jsonl_path, "w", encoding="utf-8") as f:
            for c in all_chunks:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
        print(f"\n[Success] 전체 병합본: {len(all_chunks)}개 청크 -> {jsonl_path}")
    else:
        print("\n[Warning] 생성된 청크가 없습니다. 파일 경로와 데이터를 다시 확인해주세요.")

if __name__ == "__main__":
    main()