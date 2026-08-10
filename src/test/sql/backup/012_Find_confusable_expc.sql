-- ============================================================================
-- find_confusable_expc.sql
-- 법령해석례(expc)는 title="법령해석례: {안건명} (회답/질의요지/이유-N)" 구조입니다.
-- 같은 안건명이 여러 expc_id로 존재하는지(=재상정/재질의 등으로 실제 중복인지),
-- 그리고 content에 안건명/출처가 이미 포함돼 있는지부터 확인합니다.
-- ============================================================================

-- [1] conclusion(회답) 청크 기준, 동일 안건명(title)이 여러 expc_id로 존재하는 경우
SELECT title, COUNT(*) AS def_count,
       array_agg(chunk_id ORDER BY chunk_id) AS chunk_ids
FROM legal_chunks
WHERE doc_type = 'expc'
  AND chunk_id LIKE '%\_conclusion'
GROUP BY title
HAVING COUNT(*) > 1
ORDER BY def_count DESC
LIMIT 30;

-- "법령해석례: 국토교통부ㆍ감사원ㆍ고용노동부ㆍ민원인 - 수도권 과밀억제권역, 성장관리권역 및 자연보전권역에서 제한되는 공공 청사의 ‘신축’의 범위(「수도권정비계획법 시행령」 제11조제2호가목 등 관련) (회답)"	21	"{expc_335291_conclusion,expc_335299_conclusion,expc_335303_conclusion,expc_335305_conclusion,expc_335307_conclusion,expc_335309_conclusion,expc_335311_conclusion,expc_335321_conclusion,expc_335323_conclusion,expc_335333_conclusion,expc_335337_conclusion,expc_335343_conclusion,expc_335345_conclusion,expc_335347_conclusion,expc_335353_conclusion,expc_335355_conclusion,expc_335361_conclusion,expc_335365_conclusion,expc_335367_conclusion,expc_335371_conclusion,expc_335373_conclusion}"
-- "법령해석례: 민원인, 농림축산식품부 - 건축주 변경신고로 농지전용 변경허가를 의제받을 수 있는지 등(「건축법」 제11조제5항 등 관련) (회답)"	11	"{expc_332457_conclusion,expc_332475_conclusion,expc_332477_conclusion,expc_332487_conclusion,expc_332491_conclusion,expc_332495_conclusion,expc_332497_conclusion,expc_332499_conclusion,expc_332501_conclusion,expc_332503_conclusion,expc_332507_conclusion}"
-- "법령해석례: 민원인ㆍ국토교통부 - 「주택법」 제11조의3제5항제1호에 따른 “이미 신고된 사업대지와 전부 또는 일부가 중복되는 경우”의 의미(「주택법」 제11조의3 등) (회답)"	10	"{expc_335285_conclusion,expc_335301_conclusion,expc_335315_conclusion,expc_335317_conclusion,expc_335327_conclusion,expc_335341_conclusion,expc_335351_conclusion,expc_335357_conclusion,expc_335359_conclusion,expc_335369_conclusion}"
-- "법령해석례: 행정안전부ㆍ민원인 - 대통령령 제33312호 「공무원 여비 규정」 시행 전에 국내 교육훈련이 시작된 공무원에게 지급하는 여비의 지급 기준(대통령령 제33312호 「공무원 여비 규정」 부칙 제2조 등 관련) (회답)"	9	"{expc_337297_conclusion,expc_337299_conclusion,expc_337301_conclusion,expc_337303_conclusion,expc_337305_conclusion,expc_337307_conclusion,expc_337309_conclusion,expc_337311_conclusion,expc_337313_conclusion}"
-- "법령해석례: 문화체육관광부ㆍ경상북도 경주시 - 관광사업 시설에 대한 경매 등의 절차 진행 중에 종전 관광사업자의 폐업으로 관광사업 등록이 말소된 이후 해당 관광사업 시설의 전부를 경매 등을 통해 인수한 경우 종전 관광사업자의 지위를 승계하는지 여부(「관광진흥법」 제8조제2항 등 관련) (회답)"	8	"{expc_336515_conclusion,expc_336535_conclusion,expc_336539_conclusion,expc_336541_conclusion,expc_336543_conclusion,expc_336545_conclusion,expc_336547_conclusion,expc_336549_conclusion}"
-- "법령해석례: 문화체육관광부, 민원인 - 타인 소유 부동산의 사용권을 확보하여 체육시설업을 하던 자가 그 부동산의 사용권을 상실한 경우 체육시설업 등록 등의 취소가 가능한지 여부 등(「체육시설의 설치ㆍ이용에 관한 법률」 제31조 등 관련) (회답)"	8	"{expc_332159_conclusion,expc_332163_conclusion,expc_332167_conclusion,expc_332171_conclusion,expc_332173_conclusion,expc_332175_conclusion,expc_332179_conclusion,expc_332181_conclusion}"
-- "법령해석례: 문화체육관광부 - 관광사업자의 타인 경영 등이 가능한 유기시설 등의 범위(「관광진흥법」 제11조제1항제4호 등 관련) (회답)"	8	"{expc_337381_conclusion,expc_337391_conclusion,expc_337393_conclusion,expc_337395_conclusion,expc_337397_conclusion,expc_337399_conclusion,expc_337401_conclusion,expc_337403_conclusion}"
-- "법령해석례: 민원인 및 부산광역시 남구 - 재개발사업 정비계획에 따른 존치 건축물의 소유자가 조합 설립 시 동의를 받아야 하는 “토지등소유자”에 해당하는지 여부 등(「도시 및 주거환경정비법」 제35조제2항 등 관련) (회답)"	8	"{expc_326281_conclusion,expc_327599_conclusion,expc_328483_conclusion,expc_328491_conclusion,expc_328701_conclusion,expc_328731_conclusion,expc_330013_conclusion,expc_330645_conclusion}"
-- "법령해석례: 부산광역시교육청ㆍ민원인 - 「초ㆍ중등교육법」 제2조에 따른 학교의 장 등이 「결핵예방법」 제11조제1항 본문에 따라 결핵검진과 잠복결핵검진을 실시해야 하는 대상의 범위(「결핵예방법」 제11조제1항 등 관련) (회답)"	8	"{expc_335835_conclusion,expc_335837_conclusion,expc_335839_conclusion,expc_335841_conclusion,expc_335843_conclusion,expc_335845_conclusion,expc_335849_conclusion,expc_335851_conclusion}"
-- "법령해석례: 인사혁신처ㆍ행정안전부 - 청원경찰 또는 별정우체국 직원이 징계로 파면 또는 해임처분을 받은 경우가 공무원 결격사유에 해당하는지 여부(「국가공무원법」 제33조제7호ㆍ제8호 및 「지방공무원법」 제31조제7호ㆍ제8호 등 관련) (회답)"	8	"{expc_340121_conclusion,expc_340123_conclusion,expc_340125_conclusion,expc_340127_conclusion,expc_340129_conclusion,expc_340131_conclusion,expc_340133_conclusion,expc_340139_conclusion}"
-- "법령해석례: 서울특별시 종로구ㆍ서울특별시 마포구ㆍ부산광역시 부산진구ㆍ경기도 광명시 - 생활소음ㆍ진동 규제 기준이 적용되는 공사장의 범위(「소음ㆍ진동관리법 시행규칙」 별표 8 제1호가목ㆍ나목 등 관련) (회답)"	8	"{expc_337803_conclusion,expc_337811_conclusion,expc_337813_conclusion,expc_337829_conclusion,expc_337831_conclusion,expc_337833_conclusion,expc_337837_conclusion,expc_337841_conclusion}"
-- "법령해석례: 산림청 - 「산림자원의 조성 및 관리에 관한 법률」 제36조제7항에 따라 허가 또는 신고 없이 벌채할 수 있는 경우에 해당하면 산림 소유자의 동의가 없어도 되는지(「산림자원의 조성 및 관리에 관한 법률 시행규칙」 제47조 등 관련) (회답)"	7	"{expc_327243_conclusion,expc_327893_conclusion,expc_328827_conclusion,expc_329169_conclusion,expc_329245_conclusion,expc_329567_conclusion,expc_331261_conclusion}"
-- "법령해석례: 국토교통부ㆍ서울특별시교육청 - 대수선에 해당하는 건축물 외벽에 사용하는 마감재료 증설의 범위(「건축법 시행령」 제3조의2제9호 등 관련) (회답)"	7	"{expc_337147_conclusion,expc_337185_conclusion,expc_337187_conclusion,expc_337189_conclusion,expc_337191_conclusion,expc_337193_conclusion,expc_337195_conclusion}"
-- "법령해석례: 행정안전부ㆍ세종특별자치시ㆍ민원인 - 세종특별자치시에 대하여 「지방교부세법 시행규칙」 별표 2에 따라 측정항목별 표준행정수요액을 산정할 때 적용되는 산정방식(「세종특별자치시 설치 등에 관한 특별법」 제8조제1항 및 「지방교부세법 시행규칙」 별표 2 등 관련) (회답)"	6	"{expc_338319_conclusion,expc_338359_conclusion,expc_338361_conclusion,expc_338363_conclusion,expc_338365_conclusion,expc_338367_conclusion}"
-- "법령해석례: 공정거래위원회ㆍ서울특별시 강남구 - 위반행위를 직권으로 조사할 수 있는 행정청의 범위 등(「전자상거래 등에서의 소비자보호에 관한 법률」 제26조 등 관련) (회답)"	6	"{expc_335051_conclusion,expc_335071_conclusion,expc_335087_conclusion,expc_335091_conclusion,expc_335095_conclusion,expc_335097_conclusion}"
-- "법령해석례: 충청남도 공주시ㆍ민원인 - 골재채취업의 양수인은 골재채취의 허가 등에 따른 권리ㆍ의무를 승계하는지 여부(「골재채취법」 제17조제2항 등 관련) (회답)"	6	"{expc_336953_conclusion,expc_336969_conclusion,expc_336971_conclusion,expc_336981_conclusion,expc_336983_conclusion,expc_336985_conclusion}"
-- "법령해석례: 세종특별자치시, 행정중심복합도시건설청 - 예정지역에서 지구단위계획에 위반한 건축물의 건축 등에 대하여 제재처분을 할 수 있는 행정청이 어디인지 등(「신행정수도 후속대책을 위한 연기ㆍ공주지역 행정중심복합도시 건설을 위한 특별법」 제14조 등 관련) (회답)"	6	"{expc_327869_conclusion,expc_327875_conclusion,expc_328481_conclusion,expc_328659_conclusion,expc_329365_conclusion,expc_329787_conclusion}"
-- "법령해석례: 식품의약품안전처ㆍ민원인 - 공사용 가설건축물에서 일반음식점영업 또는 집단급식소를 설치ㆍ운영할 수 있는지 여부 등(「식품위생법 시행규칙」 제42조 등 관련) (회답)"	6	"{expc_326377_conclusion,expc_328139_conclusion,expc_329115_conclusion,expc_329383_conclusion,expc_329685_conclusion,expc_331389_conclusion}"
-- "법령해석례: 인천광역시 중구ㆍ전라남도 목포시 - 항만에서 행하는 「항만법 시행령」 제35조 각 호의 행위가  ‘항만의 보전 또는 그 사용에 지장을 줄 우려가 있는 행위’인지를 별도로 살피지 않아도 「항만법」 제28조제1항제3호에 따른 금지행위에 해당하는지(「항만법」 제28조 등) (회답)"	6	"{expc_336955_conclusion,expc_336961_conclusion,expc_336973_conclusion,expc_336975_conclusion,expc_336977_conclusion,expc_336979_conclusion}"
-- "법령해석례: 민원인 - 「영유아보육법」상 국공립어린이집 외의 어린이집은  「사회복지사업법 시행규칙」 제20조제4항에 따른 사회복지시설신고관리대장을 작성ㆍ관리하여야 하는 대상인지(「사회복지사업법 시행규칙」 제20조제4항 등 관련) (회답)"	6	"{expc_339099_conclusion,expc_339101_conclusion,expc_339103_conclusion,expc_339105_conclusion,expc_339107_conclusion,expc_339109_conclusion}"
-- "법령해석례: 농림축산식품부, 중소벤처기업부, 민원인 - 농지보전부담금 면제 요건을 갖추어야 하는 시기 등(「농지법」 제38조제6항 등 관련) (회답)"	6	"{expc_332201_conclusion,expc_332205_conclusion,expc_332221_conclusion,expc_332223_conclusion,expc_332227_conclusion,expc_332251_conclusion}"
-- "법령해석례: 국가교육위원회ㆍ교육부ㆍ인사혁신처 - 국가교육위원회의 위원장과 상임위원이 비영리직무등을 수행하려는 경우 별도의 사전 허가 절차를 거쳐야 하는지 여부(「국가교육위원회의 설치 및 운영에 관한 법률」 제9조 등 관련) (회답)"	6	"{expc_335463_conclusion,expc_335471_conclusion,expc_335475_conclusion,expc_335477_conclusion,expc_335479_conclusion,expc_335481_conclusion}"
-- "법령해석례: 민원인 - 「노인복지법」에 따른 65세 이상이 되는 첫 번째 날은 언제인지(「노인복지법」 제26조제1항 등 관련) (회답)"	5	"{expc_334909_conclusion,expc_334911_conclusion,expc_334917_conclusion,expc_334919_conclusion,expc_334921_conclusion}"
-- "법령해석례: 민원인 - 법령 개정으로 폐지된 단독주택재건축사업에 관한 경과조치의 적용범위(대통령령 제24007호 「도시 및 주거환경정비법 시행령」 부칙 제6조제1항 등 관련) (회답)"	5	"{expc_335853_conclusion,expc_335855_conclusion,expc_335859_conclusion,expc_335863_conclusion,expc_335865_conclusion}"
-- "법령해석례: 민원인 - 대통령령 제35134호 「국가연구개발혁신법 시행령」 시행 당시 진행 중인 연구개발과제 수행에 참여하였던 연구자 등에 대해서 같은 영 시행 전에 종료된 육아휴직의 경우에도 해당 육아휴직 기간 동안 연구개발기관이 부담한 고용보험료 및 산재보험료 등의 비용이 간접비의 사용용도에 해당하는지(대통령령 제35134호 「국가연구개발혁신법 시행령」 별표 2 제2호가목4) 등 관련)   (회답)"	5	"{expc_342381_conclusion,expc_342383_conclusion,expc_342389_conclusion,expc_342391_conclusion,expc_342399_conclusion}"
-- "법령해석례: 국토교통부, 감사원 - 택지 개발ㆍ분양 예정 토지에서의 주택건설사업계획 승인 신청 시 토지 소유권을 확보하지 못한 경우 제출해야 하는 서류 등(「주택법 시행규칙」 제12조제4항제2호 등 관련) (회답)"	4	"{expc_343047_conclusion,expc_343049_conclusion,expc_343051_conclusion,expc_343053_conclusion}"
-- "법령해석례: 국토교통부 - 국내ㆍ국제항공운송사업자가 상호 또는 주소를 변경하는 변경면허 신청서를 제출한 경우, 국토교통부장관은 변경하려는 면허내용과 관련이 없는 면허기준 충족 여부 및 결격사유 해당 여부도 심사해야 하는지 여부(「항공사업법 시행규칙」 제8조제9항 등 관련) (회답)"	4	"{expc_342531_conclusion,expc_342569_conclusion,expc_342591_conclusion,expc_342595_conclusion}"
-- "법령해석례: 민원인 - 「민간임대주택에 관한 특별법」 제6조제5항에 따른 임대사업자의 등록이 말소되는 경우 시장ㆍ군수ㆍ구청장의 청문 및 공고 의무 존재 여부(「민간임대주택에 관한 특별법」 제6조제5항 등 관련) (회답)"	4	"{expc_337947_conclusion,expc_337981_conclusion,expc_337983_conclusion,expc_337985_conclusion}"
-- "법령해석례: 국토교통부 및 충청남도 - 전국을 관할하는 공공기관이 「도청이전을 위한 도시건설 및 지원에 관한 특별법」 제2조제2호에 따른 “이전기관”에 해당하는지 여부 등(「도청이전을 위한 도시건설 및 지원에 관한 특별법」 제2조제2호 등 관련) (회답)"	4	"{expc_326407_conclusion,expc_328475_conclusion,expc_329315_conclusion,expc_330133_conclusion}"
-- "법령해석례: 국토교통부ㆍ경기도 - 대도시권광역교통위원회에 시내버스운송사업 면허 등의 권한이 위임되는 직행좌석형 시내버스운송사업 노선의 범위(「여객자동차 운수사업법 시행령」 제37조제1항제1호 및 별표 1의3 등 관련) (회답)"	4	"{expc_335757_conclusion,expc_335759_conclusion,expc_335761_conclusion,expc_335765_conclusion}"

-- [2] content 구조 샘플 (안건명/출처 법령이 content에 이미 포함돼 있는지 확인)
SELECT chunk_id, doc_id, title, content
FROM legal_chunks
WHERE doc_type = 'expc'
ORDER BY chunk_id
LIMIT 5;

-- "expc_311318_conclusion"	"311318"	"법령해석례: 국민고충처리위원회-「산업재해보상보험법」 제48조제1항 및 「국민건강보험법」 제48조제1항제4호(중복 보험급여의 제한) (회답)"	"안건명: 국민고충처리위원회-「산업재해보상보험법」 제48조제1항 및 「국민건강보험법」 제48조제1항제4호(중복 보험급여의 제한)
-- [회답]
-- 「산업재해보상보험법」상 보험급여가 지급되지 않는 치료종결후의 후유증상에 대하여 해당 근로자의 사용자는 동법 제48조제1항의 규정에 따른 「근로기준법」상의 재해보상책임 면제를 적용받지 못하므로, 이러한 후유증상은 「국민건강보험법」 제48조제1항제4호의 보험급여 제한대상에 해당한다고 할 것입니다."
-- "expc_311318_question"	"311318"	"법령해석례: 국민고충처리위원회-「산업재해보상보험법」 제48조제1항 및 「국민건강보험법」 제48조제1항제4호(중복 보험급여의 제한) (질의요지)"	"안건명: 국민고충처리위원회-「산업재해보상보험법」 제48조제1항 및 「국민건강보험법」 제48조제1항제4호(중복 보험급여의 제한)
-- [질의요지]
-- 「산업재해보상보험법」상 보험급여가 지급되지 않는 치료종결후의 후유증상에 대하여 해당 근로자의 사용자가 동법 제48조제1항에 따라 「근로기준법」상의 재해보상책임을 여전히 면제받는지 여부 및 이러한 후유증상이 「국민건강보험법」 제48조제1항제4호의 보험급여 제한대상인지 여부"
-- "expc_311318_reasoning_1"	"311318"	"법령해석례: 국민고충처리위원회-「산업재해보상보험법」 제48조제1항 및 「국민건강보험법」 제48조제1항제4호(중복 보험급여의 제한) (이유-1)"	"안건명: 국민고충처리위원회-「산업재해보상보험법」 제48조제1항 및 「국민건강보험법」 제48조제1항제4호(중복 보험급여의 제한)
-- [이유]
-- ○ 「근로기준법」 제81조의 규정에 의하면, 근로자가 업무상 부상 또는 질병에 걸린 경우에 사용자로 하여금 그 비용으로 필요한 요양을 행하거나 또는 필요한 요양비를 부담하도록 하되, 동법 제90조에는 보상을 받게 될 자가 동일한 사유에 대하여 민법 기타 법령에 의하여 동법의 재해보상에 상당한 금품을 받을 경우에는 그 가액의 한도내에서 사용자는 보상의 책임을 면하도록 하고 있으며, 「산업재해보상보험법」 제48조제1항에서는 수급권자가 이 법에 의하여 보험급여를 받았거나 받을 수 있는 경우에는 보험가입자는 동일한 사유에 대하여 「근로기준법」에 의한 재해보상책임이 면제되도록 하고 있는바, 이는 국가가 보험자의 입장에서 근로자에게 직접 보상하려는 것으로서, 사용자가 산업재해보상보험에 가입하여 당해 사고에 대하여 마땅히 보험급여가 지급되어야 하는 경우라면 사용자로 하여금 「근로기준법」에 의한 재해보상책임을 면하게 하자는 것입니다(대법원 2001. 9. 18. 선고 2001다7834 판결 참조)."
-- "expc_311318_reasoning_2"	"311318"	"법령해석례: 국민고충처리위원회-「산업재해보상보험법」 제48조제1항 및 「국민건강보험법」 제48조제1항제4호(중복 보험급여의 제한) (이유-2)"	"안건명: 국민고충처리위원회-「산업재해보상보험법」 제48조제1항 및 「국민건강보험법」 제48조제1항제4호(중복 보험급여의 제한)
-- [이유]
-- ○ 이와 관련하여 「산업재해보상보험법」 제38조 등에서는 보험급여의 종류로 요양급여, 휴업급여, 장해급여, 간병급여, 유족급여, 상병보상연금, 장의비 등을 규정하고 있으나, 후유증상에 대하여는 동법에 의한 보험급여 대상으로 규정하지 아니한 채 동법 제45조의2에서 “공단은 제40조의2의 규정에 의한 재요양의 요건에 해당하지 아니하나, 당해 업무상의 부상 또는 질병의 특성상 치유된 후에 후유증상이 발생되었거나 발생될 우려가 있는 자에 대하여는 공단이 지정한 의료기관에서 필요한 조치를 받도록 할 수 있다”라고만 규정하고 있어, 이 사안과 같이 동법 시행규칙 제16조에 따라 치료종결한 후의 후유증상에 대하여는 동법상 보험급여가 지급되지 않으며 근로복지공단에서 별도로 정한 재량적 기준에 따라 필요한 조치만을 받을 수 있으므로, 동법 제48조제1항의 요건인 “이 법에 의하여 보험급여를 받았거나 받을 수 있는 경우”에 해당되지 않으며, 설령 당초 업무상 재해로 인하여 지급된 요양급여를 들어 보험급여를 받은 경우에 해당한다고 주장하더라도, 당초 요양급여의 대상이 된 손해와 후유증상의 손해가 같은 성질을 띠는 것으로 보기 어려우므로 동항의 “동일한 사유”에 해당한다고 할 수 없습니다(대법원 1991. 7. 23. 선고 90다11776 판결 참조)."
-- "expc_311318_reasoning_3"	"311318"	"법령해석례: 국민고충처리위원회-「산업재해보상보험법」 제48조제1항 및 「국민건강보험법」 제48조제1항제4호(중복 보험급여의 제한) (이유-3)"	"안건명: 국민고충처리위원회-「산업재해보상보험법」 제48조제1항 및 「국민건강보험법」 제48조제1항제4호(중복 보험급여의 제한)
-- [이유]
-- ○ 더욱이 「산업재해보상보험법」상의 보험급여대상과 「근로기준법」상의 보상대상이 대부분 중복된다고 할지라도 일정 부분 차이가 있고, 「산업재해보상보험법」은 보험급여가 지급되는 부분과 지급되지 아니하는 부분을 구분하여 규정하고 있으나, 「근로기준법」에서는 이러한 구분 없이 업무상의 재해로 인정되는 한 보상하도록 하고 있는바, 「산업재해보상보험법」 제48조제1항의 규정은 동법상의 보험급여와 「근로기준법」의 재해보상 대상이 중복되는 경우에 한하여 적용되는 것이므로 「산업재해보상보험법」상 보험급여가 지급되지 아니하는 후유증상에 대하여는 「근로기준법」 제81조에 의하여 계속적인 요양보상을 받을 수 밖에 없으며, 「근로기준법」 제90조의 규정을 보더라도 그 업무상 재해에 대하여 산업재해보상보험으로 보상이 되지 아니하는 등 불충분한 경우에는 여전히 「근로기준법」상의 사용자는 그 불충분한 부분에 대하여 재해보상 책임을 면할 수 없다고 할 것이어서, 치료종결 후의 후유증상에 대하여 근로자가 산업재해보상보험에 의한 보험급여를 지급받지 못하는 경우에 사용자는 여전히 「근로기준법」 제81조의 규정에 의한 요양보상책임이 있다고 하겠습니다."

-- [3] 전체 규모 및 conclusion/question/reasoning 구성 비율
SELECT
    COUNT(*) AS total_expc_chunks,
    COUNT(*) FILTER (WHERE chunk_id LIKE '%\_conclusion') AS conclusion_count,
    COUNT(*) FILTER (WHERE chunk_id LIKE '%\_question') AS question_count,
    COUNT(*) FILTER (WHERE chunk_id LIKE '%\_reasoning\_%') AS reasoning_count
FROM legal_chunks
WHERE doc_type = 'expc';

-- 52407	8802	8803	34802

-- [4] prec 때처럼 내용이 비정상적으로 짧은(거의 빈) 청크가 있는지 분포 확인
SELECT
    width_bucket(length(content), 0, 500, 10) AS len_bucket,
    MIN(length(content)) AS min_len,
    MAX(length(content)) AS max_len,
    COUNT(*) AS chunk_count
FROM legal_chunks
WHERE doc_type = 'expc'
GROUP BY len_bucket
ORDER BY len_bucket;

-- 1	39	49	8
-- 2	50	99	421
-- 3	100	149	1736
-- 4	150	199	3382
-- 5	200	249	4085
-- 6	250	299	3751
-- 7	300	349	3357
-- 8	350	399	2711
-- 9	400	449	2230
-- 10	450	499	2025
-- 11	500	3093	28701