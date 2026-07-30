import json
import os
from datetime import datetime
import re
import struct
from dataclasses import dataclass
from enum import Enum

# 🎯 탐색할 특정 경로 고정 (필요 시 수정 가능)
TARGET_DIR = r"C:\Users\foodg\Documents\Black Desert\UserCache\3633053\91"


def find_latest_in_tree(base_dir, needle):
    """지정한 폴더 및 모든 하위 폴더를 뒤져 가장 최근에 수정된 파일 하나를 찾습니다."""
    latest_file = None
    latest_time = 0

    for root, dirs, files in os.walk(base_dir):
        for file in files:
            if file == needle:
                file_path = os.path.join(root, file)
                ts = os.path.getmtime(file_path)
                if ts > latest_time:
                    latest_file = file_path
                    latest_time = ts

    if not latest_file:
        raise FileNotFoundError(f"'{base_dir}' 및 하위 폴더에서 '{needle}' 파일을 찾지 못했습니다.")

    print(f'using {latest_file} modified {datetime.fromtimestamp(latest_time)}')
    return latest_file


def parse_worker_names_from_tree(base_dir):
    """지정한 폴더 및 모든 하위 폴더의 gamevariable.xml을 조사하여 일꾼 이름을 수집합니다."""
    wid_names = {}
    xml_count = 0

    for root, dirs, files in os.walk(base_dir):
        for file in files:
            if file == "gamevariable.xml":
                file_path = os.path.join(root, file)
                xml_count += 1
                try:
                    with open(file_path, "r", encoding='utf-8', errors='ignore') as f:
                        text = f.read()

                    # workerNo="숫자" name="이름" 패턴 탐색
                    matches1 = re.findall(r'workerNo="(\d+)".*?name="([^"]+)"', text)
                    # name="이름" workerNo="숫자" (순서가 바뀐 경우) 탐색
                    matches2 = re.findall(r'name="([^"]+)".*?workerNo="(\d+)"', text)

                    for wid, name in matches1:
                        wid_names[int(wid)] = name
                    for name, wid in matches2:
                        wid_names[int(wid)] = name
                except Exception:
                    continue

    print(f'총 {xml_count}개의 gamevariable.xml 파일 탐색 완료')
    if not wid_names:
        print("⚠️ 일꾼 이름을 찾지 못했습니다. (일꾼 ID 번호로 진행됩니다)")
    else:
        print(f'parsed {len(wid_names)} worker names')

    return wid_names


class JobType(Enum):
    UNKNOWN = -1
    PLANTZONE = 0
    RENTHOUSE = 2
    RENTHOUSELARGE = 6
    HARVESTING = 9


@dataclass
class WorkerRecord:
    wid: int
    charkey: int
    tnk: int
    level: int
    mspdLvlup: int              # sheet = static * (1 + lvlup / 1000000)
    wspdLvlup: int              # sheet = static + lvlup / 1000000
    luckLvlup: int              # sheet = static + lvlup / 10000
    skills: tuple[int]
    jobtype: int
    repeats: int
    workplace: int
    label: str

    @classmethod
    def from_file(cls, f, label=''):
        raw = f.read(68)
        tup = struct.unpack("=QHIIIII9HBH", raw[:51])
        fields = (*tup[:7], tup[7:16], *tup[16:], 0, label)
        me = cls(*fields)
        if me.jobtype == 0:  # plantzone
            exk, pzk = struct.unpack("HH", raw[51:55])
            me.workplace = pzk
        if me.jobtype == 2:  # renthouse
            exk, hk = struct.unpack("HH", raw[51:55])
            me.workplace = hk
        if me.jobtype == 6:  # renthouse large
            hk, = struct.unpack("H", raw[51:53])
            me.workplace = hk
        return me

    def to_dict(self):
        d = self.__dict__.copy()
        d.pop('wid')
        d.pop('repeats')

        if d['label'] == '':
            d['label'] = str(self.wid % 10000000000000000)

        d.pop('jobtype')
        d.pop('workplace')

        if self.jobtype == 0:           # plantzone
            d['job'] = self.workplace
        elif self.jobtype == 2:         # renthouse
            d['job'] = {'kind': 'workshop', 'hk': self.workplace}
        elif self.jobtype == 6:         # renthouse large
            d['job'] = {'kind': 'workshop', 'hk': self.workplace}
        elif self.jobtype == 9:         # harvesting
            d['job'] = 'farming'
        else:
            d['job'] = None

        return d


def parse_worker_data(filename, wid_names):
    workers = []
    with open(filename, "rb") as f:
        head, count, version = struct.unpack("4sII", f.read(12))
        print(f"ℹ️ worker.cache 파일 헤더: {head}, 일꾼 수: {count}, 버전: {version}")
        
        assert head == b'PABR', f"유효하지 않은 worker.cache 파일 헤더입니다. ({head})"
        
        # 버전 2 조건 강제 검사를 주석 처리하여 우회
        # assert version == 2, "지원하지 않는 worker.cache 버전입니다."
        if version != 2:
            print(f"⚠️ 경고: 기존 버전(2)과 다른 버전({version})입니다. 진행을 시도합니다.")

        for _ in range(count):
            worker = WorkerRecord.from_file(f)
            if worker.wid in wid_names:
                worker.label = wid_names[worker.wid]
            workers.append(worker)

    print('parsed', len(workers), 'workers')
    return workers


def to_workerman(workers, filename):
    out = {
        "activateAncado": False,
        "lodgingTaken": {},
        "lodgingP2W": {},
        'userWorkers': workers,
    }
    with open(filename, "w", encoding='utf-8') as f:
        json.dump(out, f, default=WorkerRecord.to_dict, indent=2, ensure_ascii=False)
        print('output written to:', os.path.abspath(filename))


def main():
    target = TARGET_DIR

    if not os.path.exists(target):
        print(f"❌ 경로를 찾을 수 없습니다: {target}")
        target = input("올바른 폴더 경로를 직접 입력하세요: ").strip()

    print(f"🔍 다음 경로 및 하위 폴더 수색 중: {target}")
    
    try:
        # 1. 91 폴더 하위 전체에서 gamevariable.xml 수집 및 일꾼 이름 수집
        wid_names = parse_worker_names_from_tree(target)
        
        # 2. 91 폴더 하위 전체에서 가장 최근 수정된 worker.cache 탐색
        cache_path = find_latest_in_tree(target, 'worker.cache')
        
        # 3. 데이터 파싱 및 출력
        worker_list = parse_worker_data(cache_path, wid_names)
        worker_list.sort(key=lambda w: w.tnk)

        to_workerman(worker_list, "to_workerman.json")
        print("\n✅ 성공적으로 to_workerman.json 파일이 생성되었습니다.")

    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")

    input("\nEnter 키를 누르면 종료합니다...")


if __name__ == "__main__":
    main()