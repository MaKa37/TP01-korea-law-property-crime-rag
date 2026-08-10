"""
merge_eval_dataset.py
======================
오늘 만든 하드케이스 문항들(law 10개, prec 3개, lstrm 4개 = 총 17개)을
기존 eval_dataset.json에 병합합니다.

HARD_PREC_01은 의도적으로 제외합니다 (죄명이 완전히 같은 별개 사건이 실존해
정답이 2개인 결함 문항으로 판명됨).

사용법:
    python src/data_merge/Merge_eval_dataset_v2.py
"""

import argparse
import json
from pathlib import Path

# 스크립트 위치(src/data_merge)를 기준으로 3단계 위가 프로젝트 루트입니다.
PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
DATASET_DIR = PROJECT_DIR / "data" / "dataset"
DEFAULT_EVAL_DATASET_PATH = DATASET_DIR / "eval_dataset.json"

ADDITION_FILENAMES = [
    "eval_dataset_additions_base.json",
    "eval_dataset_additions_law.json",
    "eval_dataset_additions_prec.json",
    "eval_dataset_additions_prec_b.json",
    "eval_dataset_additions_lstrm.json",
]

EXCLUDE_IDS = {"HARD_PREC_01"}  # 정답이 2개인 결함 문항


def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="하드케이스 평가 문항을 eval_dataset.json에 병합")
    parser.add_argument("--eval-dataset", type=str, default=str(DEFAULT_EVAL_DATASET_PATH))
    # 기본 경로를 루트 하위의 data/dataset 으로 고정
    parser.add_argument("--additions-dir", type=str, default=str(DATASET_DIR),
                        help="eval_dataset_additions_*.json 파일들이 있는 디렉터리 (기본값: data/dataset)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    eval_dataset_path = Path(args.eval_dataset)
    additions_dir = Path(args.additions_dir)

    if not eval_dataset_path.exists():
        raise FileNotFoundError(f"기존 평가셋을 찾을 수 없습니다: {eval_dataset_path}")

    dataset = load_json(eval_dataset_path)
    existing_ids = {item.get("id") for item in dataset}
    print(f"📂 기존 평가셋: {len(dataset)}문항 ({eval_dataset_path})")
    print(f"🔍 추가 파일 탐색 경로: {additions_dir}")

    added_total = 0
    for filename in ADDITION_FILENAMES:
        addition_path = additions_dir / filename

        if not addition_path.exists():
            print(f"⚠️  건너뜀 (파일 없음): {filename}")
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

        print(f"✅ {addition_path.name}: {added_from_file}문항 추가")
        added_total += added_from_file

    if added_total == 0:
        print("\n🛑 추가된 문항이 없어 저장을 건너뜁니다. 파일 위치를 확인하세요.")
        return

    # 병합 전 백업
    backup_path = eval_dataset_path.with_suffix(".json.bak")
    with open(backup_path, "w", encoding="utf-8") as f:
        # 백업 시에도 원본 파일을 다시 읽어서 저장(안전 보장)
        json.dump(load_json(eval_dataset_path), f, ensure_ascii=False, indent=2)
    print(f"💾 병합 전 원본 백업: {backup_path}")

    with open(eval_dataset_path, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)

    print(f"\n🎉 병합 완료: 총 {len(dataset)}문항 (기존 {len(dataset) - added_total} + 신규 {added_total})")


if __name__ == "__main__":
    main()