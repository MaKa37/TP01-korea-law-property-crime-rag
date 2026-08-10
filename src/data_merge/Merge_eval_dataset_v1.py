"""
merge_eval_dataset.py
======================
오늘 만든 하드케이스 문항들(law 10개, prec 3개, lstrm 4개 = 총 17개)을
기존 eval_dataset.json에 병합합니다.

HARD_PREC_01은 의도적으로 제외합니다 (죄명이 완전히 같은 별개 사건이 실존해
정답이 2개인 결함 문항으로 판명됨).

사용법:
    python merge_eval_dataset.py
    python merge_eval_dataset.py --eval-dataset data/dataset/eval_dataset.json --additions-dir .

추가 파일(eval_dataset_additions_*.json)은 기본적으로 다음 순서로 찾습니다:
    1) --additions-dir로 지정한 경로
    2) 현재 작업 디렉터리(실행한 위치)
    3) 이 스크립트 파일이 있는 디렉터리
    4) 이 스크립트의 상위 디렉터리(프로젝트 루트로 흔히 씀)
"""

import argparse
import json
from pathlib import Path
from typing import List, Optional

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_EVAL_DATASET_PATH = PROJECT_DIR / "data" / "dataset" / "eval_dataset.json"
EVAL_DATASET_PATH = PROJECT_DIR / "data" / "dataset"

ADDITION_FILENAMES = [
    "eval_dataset_additions_base.json",
    "eval_dataset_additions_law.json",
    "eval_dataset_additions_prec.json",
    "eval_dataset_additions_lstrm.json",
]

ADDITION_FILES = [EVAL_DATASET_PATH / i for i in ADDITION_FILENAMES]

EXCLUDE_IDS = {"HARD_PREC_01"}  # 정답이 2개인 결함 문항


def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def find_addition_file(filename: str, search_dirs: List[Path]) -> Optional[Path]:
    for d in search_dirs:
        candidate = d / filename
        if candidate.exists():
            return candidate
    return None


def build_search_dirs(additions_dir: Optional[str]) -> List[Path]:
    script_dir = Path(__file__).resolve().parent
    dirs = []
    if additions_dir:
        dirs.append(Path(additions_dir))
    dirs.extend([Path.cwd(), script_dir, script_dir.parent])
    # 중복 제거(순서 유지)
    seen = set()
    unique_dirs = []
    for d in dirs:
        rd = d.resolve()
        if rd not in seen:
            seen.add(rd)
            unique_dirs.append(d)
    return unique_dirs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="하드케이스 평가 문항을 eval_dataset.json에 병합")
    parser.add_argument("--eval-dataset", type=str, default=str(DEFAULT_EVAL_DATASET_PATH))
    parser.add_argument("--additions-dir", type=str, default=None,
                         help="eval_dataset_additions_*.json 파일들이 있는 디렉터리 (미지정 시 자동 탐색)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    eval_dataset_path = Path(args.eval_dataset)
    search_dirs = build_search_dirs(args.additions_dir)

    if not eval_dataset_path.exists():
        raise FileNotFoundError(f"기존 평가셋을 찾을 수 없습니다: {eval_dataset_path}")

    dataset = load_json(eval_dataset_path)
    existing_ids = {item.get("id") for item in dataset}
    print(f"📂 기존 평가셋: {len(dataset)}문항 ({eval_dataset_path})")
    print(f"🔍 추가 파일 탐색 경로: {[str(d) for d in search_dirs]}")

    added_total = 0
    for filename in ADDITION_FILES:
        addition_path = find_addition_file(filename, search_dirs)

        if addition_path is None:
            print(f"⚠️  건너뜀 (파일 없음, 탐색 경로 전부 확인함): {filename}")
            continue

        additions = load_json(addition_path)
        added_from_file = 0

        for item in additions:
            item_id = item.get("id")

            if item_id in EXCLUDE_IDS:
                print(f"   ⏭️  제외: {item_id} (결함 문항)")
                continue

            if item_id in existing_ids:
                print(f"   ⏭️  건너뜀: {item_id} (이미 존재)")
                continue

            dataset.append(item)
            existing_ids.add(item_id)
            added_from_file += 1

        print(f"✅ {addition_path}: {added_from_file}문항 추가")
        added_total += added_from_file

    if added_total == 0:
        print("\n🛑 추가된 문항이 없어 저장을 건너뜁니다. 파일 위치를 확인하세요.")
        return

    # 병합 전 백업
    backup_path = eval_dataset_path.with_suffix(".json.bak")
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(load_json(eval_dataset_path), f, ensure_ascii=False, indent=2)
    print(f"💾 병합 전 원본 백업: {backup_path}")

    with open(eval_dataset_path, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)

    print(f"\n🎉 병합 완료: 총 {len(dataset)}문항 (기존 {len(dataset) - added_total} + 신규 {added_total})")


if __name__ == "__main__":
    main()