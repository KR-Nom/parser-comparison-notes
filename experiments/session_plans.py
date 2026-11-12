"""2026-11-12 세션에서 원본을 해석해 작성한 고정 구조 계획.

이 계획은 모델 응답 재생용 코드이며 금액 정답이나 일반화된 파서 규칙이 아니다.
추가 원본 탐색으로 작성했으므로 기존 API의 독립 요청과 입력 조건이 다르다.
"""


ORG_REVENUE_SHEET="매출_세부_IFRS15後"
ORG_REVENUE_FILES=[
"01_F10_사업부1_재무제표.xlsx","02_F12_사업부2_재무제표.xlsx","03_F20_사업부3_재무제표.xlsx","04_F40_사업부4_재무제표.xlsx",
"05_F39_사업부5_재무제표.xlsx","06_F50_사업부6_재무제표.xlsx","07_F16_사업부7_재무제표.xlsx","08_F13_사업부8_재무제표.xlsx",
"09_F25_사업부9_재무제표.xlsx","10_F43_사업부10_재무제표.xlsx","11_F42_사업부11_재무제표.xlsx","12_F44_사업부12_재무제표.xlsx",
"13_F73_사업부13_재무제표.xlsx","14_F38_사업부14_재무제표.xlsx","15_F66_사업부15_재무제표.xlsx","16_F36_사업부16_재무제표.xlsx",
"17_F37_사업부17_재무제표.xlsx"]
ORG_REVENUE_ORG_ROWS=[3,20,41,71,313,368,450,516,602,653,770,797,861,870,888,1182,1202]
ORG_REVENUE_REFS={c:{"sheet":ORG_REVENUE_SHEET,"cell":c} for c in
["A1","A2","F2","A3","A5","H3","V3","AJ3","AX3","H4","V4","AJ4","AX4","I4","W4","AK4","AY4"]}
ORG_REVENUE_PERIOD_SPECS=[
("H","H",2025,"H3","annual",None,None,"unknown",["H3","H4"]),
("I","T",2025,"H3","month",4,1,"actual",["H3","I4"]),
("V","V",2026,"V3","annual",None,None,"unknown",["V3","V4"]),
("W","AH",2026,"V3","month",4,1,"plan",["V3","W4"]),
("AJ","AJ",2026,"AJ3","annual",None,None,"unknown",["AJ3","AJ4"]),
("AK","AV",2026,"AJ3","month",4,1,"actual",["AJ3","AK4"]),
("AX","AX",2027,"AX3","annual",None,None,"unknown",["AX3","AX4"]),
("AY","BJ",2027,"AX3","month",4,1,"plan",["AX3","AY4"])]
ORG_REVENUE_PERIODS=[
dict(start_col=a,end_col=b,year=y,year_ref=ORG_REVENUE_REFS[yr],aggregation=agg,
month_header_row=mh,start_month=sm,scenario=sc,scenario_ref=ORG_REVENUE_REFS[yr],
evidence=[ORG_REVENUE_REFS[c] for c in ev])
for a,b,y,yr,agg,mh,sm,sc,ev in ORG_REVENUE_PERIOD_SPECS]
organization_plans={
filename:{"sheet":ORG_REVENUE_SHEET,"kind":"revenue_detail",
"tables":[dict(start_row=5,end_row=39,start_col="A",end_col="BJ",header_start_row=3,header_end_row=4,
organization_code_col=None,organization_name_col="A",
organization_code_ref={"sheet":"조직코드","cell":f"A{orgrow}"},
organization_name_ref=ORG_REVENUE_REFS["A5"],periods=ORG_REVENUE_PERIODS,
rows=[dict(start_row=5,end_row=39,role="detail",item_col="D",item_code_col=None,
hierarchy_cols=["B","C"],ancestor_rows=[],metric_code=None,reason="관리분류·중분류·소분류와 거래내역이 있는 개별 매출 행")],
unit="천원",normalization_mode="amount_to_won",unit_evidence=[ORG_REVENUE_REFS["A1"]],
unit_overrides=[],exclusions=[],exceptions=[])],
"excluded_areas":[dict(start_row=1,end_row=4,start_col="A",end_col="BJ",
reason="제목·안내·두 줄 헤더 영역. 2행은 축약 단위의 상단 참고 합계이며 개별 거래 행이 아님",
evidence=[ORG_REVENUE_REFS["A2"],ORG_REVENUE_REFS["F2"],ORG_REVENUE_REFS["A3"]])],
"uncertainties":["연간 소제목과 상위 시나리오가 충돌하여 연간 4열은 unknown으로 보존한다."]}
for filename,orgrow in zip(ORG_REVENUE_FILES,ORG_REVENUE_ORG_ROWS)}


reference_responses = {
    (lo, hi): {"plan": {"sheet": "예산코드", "kind": "reference", "tables": [],
        "excluded_areas": [{"start_row": lo, "end_row": hi, "start_col": "A", "end_col": "F",
            "reason": "예산코드와 GL코드를 명칭 및 집계 분류에 연결하는 참조표이다.",
            "evidence": [{"sheet":"예산코드","cell":address} for address in ("A1","A2","B2","C2","E2","F2")]}],
        "uncertainties":[]}}
    for lo, hi in ((3,102),(103,202),(203,302),(303,316))
}



REV_BLOCKS=[
((2,101),[(5,39,3),(40,74,20),(75,101,41)]),
((102,201),[(102,109,41),(110,144,71),(145,179,313),(180,201,368)]),
((202,301),[(202,214,368),(215,249,450),(250,284,516),(285,301,602)]),
((302,401),[(302,319,602),(320,354,653),(355,389,770),(390,401,797)]),
((402,501),[(402,424,797),(425,459,861),(460,494,870),(495,501,888)]),
((502,599),[(502,529,888),(530,564,1182),(565,599,1202)])]
OPEX_BLOCKS=[
((3,102),[(6,29,3),(30,53,20),(54,77,41),(78,101,71),(102,102,313)]),
((103,202),[(103,125,313),(126,149,368),(150,173,450),(174,197,516),(198,202,602)]),
((203,302),[(203,221,602),(222,245,653),(246,269,770),(270,293,797),(294,302,861)]),
((303,402),[(303,317,861),(318,341,870),(342,365,888),(366,389,1182),(390,402,1202)]),
((403,413),[(403,413,1202)])]
REV_PERIODS=[
('H','H',2025,'H3','annual',None,'mixed','H4'),
('I','T',2025,'H3','month',4,'actual','H3'),
('V','V',2026,'V3','annual',None,'mixed','V4'),
('W','AH',2026,'V3','month',4,'plan','V3'),
('AJ','AJ',2026,'AJ3','annual',None,'mixed','AJ4'),
('AK','AV',2026,'AJ3','month',4,'actual','AJ3'),
('AX','AX',2027,'AX3','annual',None,'mixed','AX4'),
('AY','BJ',2027,'AX3','month',4,'plan','AX3')]
OPEX_PERIODS=[
('Q','Q',2025,'Q4','annual',None,'mixed','Q5'),
('R','AC',2025,'Q4','month',5,'actual','Q4'),
('AE','AE',2026,'AE4','annual',None,'mixed','AE5'),
('AF','AQ',2026,'AE4','month',5,'plan','AE4'),
('AS','AS',2026,'AS4','annual',None,'mixed','AS5'),
('AT','BE',2026,'AS4','month',5,'actual','AS4'),
('BG','BG',2027,'BG4','annual',None,'mixed','BG5'),
('BH','BS',2027,'BG4','month',5,'plan','BG4'),
('BU','BU',2027,'BU4','annual',None,'estimate','BU5'),
('BV','CG',2027,'BU4','month',5,'estimate','BU4')]
master_responses={}
for sname,kind,blocks,specs,firstcol,lastcol,hs,he,orgcol,itemcol,codecol,hierarchy,summaryrow in [
('매출_세부_IFRS15後','revenue_detail',REV_BLOCKS,REV_PERIODS,'H','BJ',3,4,'A','D',None,['B','C'],2),
('OpEx_세부_IFRS15後','expense_detail',OPEX_BLOCKS,OPEX_PERIODS,'Q','CG',4,5,'D','K','J',['A','B','C'],3)]:
    periods=[]
    for a,b,year,yr,agg,mr,scenario,sr in specs:
        evidence=[{'sheet':sname,'cell':yr},{'sheet':sname,'cell':sr}]
        if mr is not None:
            evidence.extend([{'sheet':sname,'cell':f'{a}{mr}'},{'sheet':sname,'cell':f'{b}{mr}'}])
        periods.append(dict(start_col=a,end_col=b,year=year,year_ref={'sheet':sname,'cell':yr},
        aggregation=agg,month_header_row=mr,start_month=None,scenario=scenario,
        scenario_ref={'sheet':sname,'cell':sr},evidence=evidence))
    for block,segments in blocks:
        excluded=[]
        if block[0]==summaryrow:
            excluded.append(dict(start_row=summaryrow,end_row=summaryrow,start_col=firstcol,end_col=lastcol,
            reason='실제 열헤더 위 배율 조정 SUBTOTAL 참고행으로 거래 상세가 아님',
            evidence=[{'sheet':sname,'cell':f'{firstcol}{summaryrow}'},{'sheet':sname,'cell':f'{firstcol}{he}'}]))
        if kind=='expense_detail':
            excluded.append(dict(start_row=max(6,block[0]),end_row=block[1],start_col='A',end_col='P',
            reason='분류·조직·과제·예산코드·설명 영역으로 금액이 아님',
            evidence=[{'sheet':sname,'cell':c} for c in ('A5','J5','O5')]))
        tables=[]
        for lo,hi,orgrefrow in segments:
            tables.append(dict(start_row=lo,end_row=hi,start_col=firstcol,end_col=lastcol,
            header_start_row=hs,header_end_row=he,organization_code_col=None,organization_name_col=orgcol,
            organization_code_ref={'sheet':'조직코드','cell':f'A{orgrefrow}'},
            organization_name_ref={'sheet':sname,'cell':f'{orgcol}{lo}'},periods=periods,
            rows=[dict(start_row=lo,end_row=hi,role='detail',item_col=itemcol,item_code_col=codecol,
            hierarchy_cols=hierarchy,ancestor_rows=[],metric_code=None,
            reason='각 행에 독립 조직명·세부 항목·거래 설명이 존재하는 상세행')],
            unit='천원',normalization_mode='amount_to_won',unit_evidence=[{'sheet':sname,'cell':'A1'}],
            unit_overrides=[],exclusions=[],exceptions=[]))
        master_responses[(sname,block)]={'plan':dict(sheet=sname,kind=kind,tables=tables,
        excluded_areas=excluded,uncertainties=[
        '상위 연도·시나리오와 연간 소제목이 상충하여 해당 연간열은 mixed로 보존함.',
        '후속 블록에 없는 실제 헤더와 조직코드를 원본 추가 탐색으로 확인함. API 독립 호출과 다른 조건임.'])}
