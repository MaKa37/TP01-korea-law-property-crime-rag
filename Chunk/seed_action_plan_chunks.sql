-- ============================================================
-- 피해자 조치 절차(Action Plan) corpus 시드 데이터
-- 이전 turn에서 정의한 law_chunks 테이블에 source_type='action_plan'으로 적재
--
-- 주의: 아래 절차 내용은 2026-07-30 기준으로 조사한 내용을 요약한 것입니다.
--       법령·기관 정책은 수시로 바뀌므로, 실제 서비스에 반영하기 전에
--       각 source_url을 직접 방문해 내용을 재검증한 뒤 사용하세요.
-- ============================================================


-- ----------------------------------------------------------
-- 대분류 1. 보이스피싱 (전기통신금융사기) - 금융감독원 피해구제 절차
-- 출처: https://www.fss.or.kr/fss/main/sub1voice.do?menuNo=200012
-- ----------------------------------------------------------

INSERT INTO law_chunks (source_type, doc_id, chunk_type, citation, content, metadata) VALUES
('action_plan', 'fss-voicephishing-refund', '절차단계', '보이스피싱 피해구제 절차 1/8단계',
 '[보이스피싱 · 금융감독원 · 1단계] 지급정지 및 피해구제 요청: 피해를 인지한 즉시 112(경찰청), 1332(금융감독원) 또는 송금한 금융회사 고객센터에 신고하고, 금융회사에 피해구제신청서를 제출해 지급정지를 요청한다.',
 '{"agency": "금융감독원", "crime_type": "보이스피싱", "step_no": 1, "total_steps": 8, "source_url": "https://www.fss.or.kr/fss/main/sub1voice.do?menuNo=200012", "last_verified_date": "2026-07-30"}'),

('action_plan', 'fss-voicephishing-refund', '절차단계', '보이스피싱 피해구제 절차 2/8단계',
 '[보이스피싱 · 금융감독원 · 2단계] 지급정지: 금융회사가 사기이용계좌 전체 잔액에 지급정지를 실행하고, 피해금이 다른 금융회사로 이체된 경우 해당 금융회사에도 지급정지를 요청한다.',
 '{"agency": "금융감독원", "crime_type": "보이스피싱", "step_no": 2, "total_steps": 8, "source_url": "https://www.fss.or.kr/fss/main/sub1voice.do?menuNo=200012", "last_verified_date": "2026-07-30"}'),

('action_plan', 'fss-voicephishing-refund', '절차단계', '보이스피싱 피해구제 절차 3/8단계',
 '[보이스피싱 · 금융감독원 · 3단계] 지급정지 통보: 금융회사가 사기이용계좌 명의인에게 지급정지 사실을 통보한다.',
 '{"agency": "금융감독원", "crime_type": "보이스피싱", "step_no": 3, "total_steps": 8, "source_url": "https://www.fss.or.kr/fss/main/sub1voice.do?menuNo=200012", "last_verified_date": "2026-07-30"}'),

('action_plan', 'fss-voicephishing-refund', '절차단계', '보이스피싱 피해구제 절차 4/8단계',
 '[보이스피싱 · 금융감독원 · 4단계] 채권소멸절차 개시 요청: 금융회사가 금융감독원에 채권소멸절차 개시공고를 요청한다.',
 '{"agency": "금융감독원", "crime_type": "보이스피싱", "step_no": 4, "total_steps": 8, "source_url": "https://www.fss.or.kr/fss/main/sub1voice.do?menuNo=200012", "last_verified_date": "2026-07-30"}'),

('action_plan', 'fss-voicephishing-refund', '절차단계', '보이스피싱 피해구제 절차 5/8단계',
 '[보이스피싱 · 금융감독원 · 5단계] 채권소멸절차 개시공고: 금융감독원이 홈페이지에 2개월간 개시공고를 하며, 이 기간 중 계좌 명의인은 사기계좌가 아니라는 소명으로 이의를 제기할 수 있다.',
 '{"agency": "금융감독원", "crime_type": "보이스피싱", "step_no": 5, "total_steps": 8, "source_url": "https://www.fss.or.kr/fss/main/sub1voice.do?menuNo=200012", "last_verified_date": "2026-07-30"}'),

('action_plan', 'fss-voicephishing-refund', '절차단계', '보이스피싱 피해구제 절차 6/8단계',
 '[보이스피싱 · 금융감독원 · 6단계] 채권소멸: 공고기간 중 이의제기가 없으면 해당 계좌의 채권이 소멸한다.',
 '{"agency": "금융감독원", "crime_type": "보이스피싱", "step_no": 6, "total_steps": 8, "source_url": "https://www.fss.or.kr/fss/main/sub1voice.do?menuNo=200012", "last_verified_date": "2026-07-30"}'),

('action_plan', 'fss-voicephishing-refund', '절차단계', '보이스피싱 피해구제 절차 7/8단계',
 '[보이스피싱 · 금융감독원 · 7단계] 피해금 환급: 금융감독원이 채권소멸일로부터 14일 이내 피해자별 환급금액을 결정하고, 금융회사가 피해자 계좌로 환급한다.',
 '{"agency": "금융감독원", "crime_type": "보이스피싱", "step_no": 7, "total_steps": 8, "source_url": "https://www.fss.or.kr/fss/main/sub1voice.do?menuNo=200012", "last_verified_date": "2026-07-30"}'),

('action_plan', 'fss-voicephishing-refund', '절차단계', '보이스피싱 피해구제 절차 8/8단계',
 '[보이스피싱 · 금융감독원 · 8단계] 전자금융거래 제한 종료: 환급 절차가 마무리되면 해당 계좌에 대한 전자금융거래 제한이 해제된다.',
 '{"agency": "금융감독원", "crime_type": "보이스피싱", "step_no": 8, "total_steps": 8, "source_url": "https://www.fss.or.kr/fss/main/sub1voice.do?menuNo=200012", "last_verified_date": "2026-07-30"}');


-- ----------------------------------------------------------
-- 대분류 2. 일반 사이버사기 (중고거래·직거래 사기 등) - 경찰청 ECRM 신고 절차
-- 출처: https://ecrm.police.go.kr/minwon/crs/quick/process
-- ----------------------------------------------------------

INSERT INTO law_chunks (source_type, doc_id, chunk_type, citation, content, metadata) VALUES
('action_plan', 'police-ecrm-cyberfraud', '절차단계', '일반 사이버사기 신고 절차 1/6단계',
 '[일반 사이버사기 · 경찰청 · 1단계] 증거자료 확보: 신고 전 신분증 사본, 계좌 이체내역서, 메신저·문자 대화내역 등 증빙자료를 미리 준비한다.',
 '{"agency": "경찰청", "crime_type": "일반 사이버사기", "step_no": 1, "total_steps": 6, "source_url": "https://ecrm.police.go.kr/minwon/crs/quick/process", "last_verified_date": "2026-07-30"}'),

('action_plan', 'police-ecrm-cyberfraud', '절차단계', '일반 사이버사기 신고 절차 2/6단계',
 '[일반 사이버사기 · 경찰청 · 2단계] 신고 경로 선택: 사이버범죄 신고시스템(ECRM) 온라인 신고, 가까운 경찰서 민원실 방문 접수, 112 신고 중 하나를 선택한다.',
 '{"agency": "경찰청", "crime_type": "일반 사이버사기", "step_no": 2, "total_steps": 6, "source_url": "https://ecrm.police.go.kr/minwon/crs/quick/process", "last_verified_date": "2026-07-30"}'),

('action_plan', 'police-ecrm-cyberfraud', '절차단계', '일반 사이버사기 신고 절차 3/6단계',
 '[일반 사이버사기 · 경찰청 · 3단계] 온라인 신고 접수: ECRM 접속 후 본인인증을 거쳐 범죄유형(사이버사기)과 세부유형(중고거래사기, 피싱 등)을 선택하고, 민원서류를 작성한 뒤 증빙자료를 첨부한다.',
 '{"agency": "경찰청", "crime_type": "일반 사이버사기", "step_no": 3, "total_steps": 6, "source_url": "https://ecrm.police.go.kr/minwon/crs/quick/process", "last_verified_date": "2026-07-30"}'),

('action_plan', 'police-ecrm-cyberfraud', '절차단계', '일반 사이버사기 신고 절차 4/6단계',
 '[일반 사이버사기 · 경찰청 · 4단계] 경찰서 출석 및 진술: 온라인 신고는 임시 접수 성격이 강하며, 원칙적으로 피해자의 경찰서 출석과 진술이 필요하다. 다만 피해자가 다수인 사건은 예외적으로 생략될 수 있다.',
 '{"agency": "경찰청", "crime_type": "일반 사이버사기", "step_no": 4, "total_steps": 6, "source_url": "https://ecrm.police.go.kr/minwon/crs/quick/process", "last_verified_date": "2026-07-30"}'),

('action_plan', 'police-ecrm-cyberfraud', '절차단계', '일반 사이버사기 신고 절차 5/6단계',
 '[일반 사이버사기 · 경찰청 · 5단계] 사건번호 발급 및 진행 확인: 접수가 완료되면 사건번호가 부여되며, 이후 진행상황은 우편이나 담당 수사관을 통해 확인한다.',
 '{"agency": "경찰청", "crime_type": "일반 사이버사기", "step_no": 5, "total_steps": 6, "source_url": "https://ecrm.police.go.kr/minwon/crs/quick/process", "last_verified_date": "2026-07-30"}'),

('action_plan', 'police-ecrm-cyberfraud', '절차단계', '일반 사이버사기 신고 절차 6/6단계',
 '[일반 사이버사기 · 경찰청 · 6단계] 접수 대상 사전 확인: 단순 환불, 배송지연, 개인 간 다툼 등 민사소송 대상 행위는 접수 대상이 아니므로 신고 전 반드시 확인한다.',
 '{"agency": "경찰청", "crime_type": "일반 사이버사기", "step_no": 6, "total_steps": 6, "source_url": "https://ecrm.police.go.kr/minwon/crs/quick/process", "last_verified_date": "2026-07-30"}');


-- ----------------------------------------------------------
-- 대분류 3. 형사절차·피해자지원 공통 (사기죄 전반) - 대검찰청
-- 출처(사건처리절차): https://www.spo.go.kr/site/spo/01/10101050200002018112210.jsp
-- 출처(피해자인권): https://www.spo.go.kr/site/spo/02/10211020100002018100812.jsp
-- ----------------------------------------------------------

INSERT INTO law_chunks (source_type, doc_id, chunk_type, citation, content, metadata) VALUES
('action_plan', 'spo-criminal-procedure-victim-support', '절차단계', '형사절차·피해자지원 공통 1/6단계',
 '[형사절차 공통 · 대검찰청 · 1단계] 고소장 제출: 피해자가 관할 경찰서 또는 검찰청에 고소장을 제출하며, 이는 수사기관이 수사를 개시하는 단서가 된다.',
 '{"agency": "대검찰청", "crime_type": "형사절차·피해자지원 공통", "step_no": 1, "total_steps": 6, "source_url": "https://www.spo.go.kr/site/spo/01/10101050200002018112210.jsp", "last_verified_date": "2026-07-30"}'),

('action_plan', 'spo-criminal-procedure-victim-support', '절차단계', '형사절차·피해자지원 공통 2/6단계',
 '[형사절차 공통 · 대검찰청 · 2단계] 수사 진행: 수사기관(사법경찰관 또는 검사)이 범죄 혐의 유무를 확인하기 위해 증거를 수집·보전한다.',
 '{"agency": "대검찰청", "crime_type": "형사절차·피해자지원 공통", "step_no": 2, "total_steps": 6, "source_url": "https://www.spo.go.kr/site/spo/01/10101050200002018112210.jsp", "last_verified_date": "2026-07-30"}'),

('action_plan', 'spo-criminal-procedure-victim-support', '절차단계', '형사절차·피해자지원 공통 3/6단계',
 '[형사절차 공통 · 대검찰청 · 3단계] 형사조정 회부(선택): 검사는 신속한 피해 회복을 위해 당사자의 신청 또는 직권으로 사건을 형사조정에 회부할 수 있으며, 조정 절차에서 합의가 이뤄질 수 있다.',
 '{"agency": "대검찰청", "crime_type": "형사절차·피해자지원 공통", "step_no": 3, "total_steps": 6, "source_url": "https://www.spo.go.kr/site/spo/01/10101050200002018112210.jsp", "last_verified_date": "2026-07-30"}'),

('action_plan', 'spo-criminal-procedure-victim-support', '절차단계', '형사절차·피해자지원 공통 4/6단계',
 '[형사절차 공통 · 대검찰청 · 4단계] 배상명령 신청: 재산범죄 피해자는 별도의 민사소송 없이, 재판이 진행 중인 형사법원에 배상명령을 신청해 민사판결과 동일한 효력의 배상을 받을 수 있다.',
 '{"agency": "대검찰청", "crime_type": "형사절차·피해자지원 공통", "step_no": 4, "total_steps": 6, "source_url": "https://www.spo.go.kr/site/spo/01/10101050200002018112210.jsp", "last_verified_date": "2026-07-30"}'),

('action_plan', 'spo-criminal-procedure-victim-support', '절차단계', '형사절차·피해자지원 공통 5/6단계',
 '[형사절차 공통 · 대검찰청 · 5단계] 피해자 지원 연계: 검찰 피해자지원실(1577-2584), 전국범죄피해자지원연합회(1577-1295) 등을 통해 상담, 의료비·생계비 지원 등을 받을 수 있다.',
 '{"agency": "대검찰청", "crime_type": "형사절차·피해자지원 공통", "step_no": 5, "total_steps": 6, "source_url": "https://www.spo.go.kr/site/spo/02/10211020100002018100812.jsp", "last_verified_date": "2026-07-30"}'),

('action_plan', 'spo-criminal-procedure-victim-support', '절차단계', '형사절차·피해자지원 공통 6/6단계',
 '[형사절차 공통 · 대검찰청 · 6단계] 형사절차 정보 통지: 사건 접수·배당, 처분 결과, 공판 개시 및 재판 결과 등 주요 정보가 피해자에게 자동으로 통지된다.',
 '{"agency": "대검찰청", "crime_type": "형사절차·피해자지원 공통", "step_no": 6, "total_steps": 6, "source_url": "https://www.spo.go.kr/site/spo/02/10211020100002018100812.jsp", "last_verified_date": "2026-07-30"}');