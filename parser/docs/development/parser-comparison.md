# 규칙 기반 파서와 AI 구조 파서 비교

운영용 규칙 기반 파서와 독립 비교군인 AI 구조 파서를 동일한 입력·검증·평가 기준으로 실행한다. OCR/VLM이나 자동 운영 전환은 이번 구현에 포함하지 않는다. 진입점은 저장소 루트의 `python3 -B parser/compare.py`다. Python 3.11 이상과 표준 라이브러리를 사용하며 별도 설치는 필요 없다.

## 실행 경계

- 두 방법은 각각 `extract_workbook → validate_extraction → 해석 → validate_provenance`를 실행한다. 같은 추출 객체나 규칙 기반의 해석 결과를 공유하지 않는다.
- 규칙 경로는 기존 `map_workbook`, AI 경로는 기존 `infer_structure`를 그대로 호출한다. 구조 해석의 수식 비율 생략·블록·repair·review 정책을 벤치마크에서 몰래 수정하지 않는다.
- 원본 파일만 엔진에 전달한다. manifest의 조직·기간·정답 금액을 metadata 또는 프롬프트에 주입하지 않는다.
- 기본 입력은 manifest에 명시된 조직 파일이다. 마스터는 기본 제외하며 `--include-master`로 별도 입력·별도 집계에 포함할 수 있다. 조직과 마스터의 캐시도 분리한다.
- 기본 실행은 콘솔과 메모리만 사용한다. 기존 AI 디스크 응답 캐시와 구조 템플릿은 읽지도 쓰지도 않는다. Data는 항상 읽기 전용이다.
- `--allow-api`는 실제 외부 요청을 실행하는 선택이다. API 키는 환경 변수 또는 기존 `parser_ai/.env`에서 읽지만 출력에 포함하지 않는다.
- 파일별 처리 전후 및 엔진·반복 간 원본 해시를 확인한다. 불일치는 유효한 비교 완료로 표시하지 않는다.

## 명령

아래의 `/path/to/Data/dummy`는 기본 17개 조직과 마스터 및 `manifest_baseline.json`이 있는 실제 경로로 바꾼다. 현재 지원하는 manifest 버전은 `baseline_strict_native_input_only_v1`이다.

```bash
# API와 결과 저장 없이 실제 시트 분기·블록 수를 확인한다.
python3 -B parser/compare.py plan /path/to/Data/dummy --include-master

# API 없이 규칙 파서 1개를 실제 실행하고 평가한다.
python3 -B parser/compare.py run /path/to/Data/dummy \
  --engines rule --max-files 1

# 두 파서의 실제 비교. 이 명령을 실행하면 유료 AI 요청이 발생할 수 있다.
python3 -B parser/compare.py run /path/to/Data/dummy \
  --allow-api --max-files 1 --max-calls 20 --review off

# 같은 조건을 3회 반복하며 반복 시작마다 캐시를 비운다.
python3 -B parser/compare.py run /path/to/Data/dummy \
  --allow-api --repeats 3 --cache cold --max-calls 300
```

`--max-calls`는 파일당이 아니라 **실험 전체**의 캐시 제외 논리 호출 상한이다. HTTP 재시도는 별도이며 금액 상한이 아니다. 호출 상한에 걸린 파일도 실패·미처리 평가에 포함된다. 실제 과금은 모델·토큰·재시도에 따라 달라지므로 보고서는 비용을 임의 추정하지 않고 `cost_usd=null`로 남긴다. 응답 사용량이 없는 실패는 `usage_missing_calls`로 구분한다.

파일 저장을 명시적으로 원하는 경우에만 `run`에 `--write-output`을 추가한다. `parser/output/comparisons/<실행 ID>/comparison.json`과 `summary.csv`를 새로 만들며 기존 결과는 덮어쓰지 않는다. `--output-dir`을 지정해도 같은 허용 디렉터리 아래여야 한다. 잘못된 경로와 기존 경로는 API 호출 전에 검사한다. 셸 리다이렉션도 파일 생성이므로 저장을 의도한 경우에만 사용한다.

## 동일 조건과 캐시

기본 모델은 기존 AI 클라이언트의 설정을 따른다. 기본 추론 강도 high, review on, HTTP timeout 240초, 출력 토큰 상한 32768을 사용한다. 실제 값은 보고서에 남는다. `--model`, `--effort`, `--review`, `--timeout`, `--max-output-tokens`, `--max-exception-calls`로 조건을 명시한다.

- `cold`: 각 반복 시작 때 응답·구조 템플릿 캐시를 비운다. 같은 반복 안의 다음 조직 파일은 앞 파일의 결과를 재사용할 수 있다.
- `warm`: 반복 간 캐시를 유지한다. **첫 번째 반복은 빈 캐시**에서 시작한다. 이후에도 실제 캐시 적중 여부는 이벤트로 확인한다.
- 서로 다른 명령 실행 사이에는 메모리 캐시가 유지되지 않는다. 방법·모델·review 설정을 변경한 실험끼리 캐시를 섞지 않는다.
- 두 엔진은 동시에 실행하지 않는다. 파일과 반복에 따라 실행 순서를 교대한다. OS 파일 캐시는 강제로 비우지 않으므로 엄밀한 저장장치 cold-cache 실험은 아니다.

`plan`은 캐시 재사용과 실패가 없을 때의 작업량 계산이다. 실제 API 호출·모델 성공률·처리 시간 측정이 아니다. review 호출은 작업 수에 반영하지만 repair·재시도·연결 실패 후 재분할은 예측하지 않는다.

## 독립 평가 계약

### 기본 manifest 금액 합계 평가

manifest는 셀별 조직·항목·기간 정답이 아니라 매출·비용의 월별 및 연간 합계를 제공한다. 따라서 `financial_aggregate_evaluation`은 **금액 합계만** 평가하며 의미 정확도를 주장하지 않는다.

기본 더미 생성 계약에 따른 평가 전용 위치는 다음과 같다. 이 위치 정보는 엔진에 전달하지 않는다.

| 영역 | 시트 | 첫 상세 행 | 연간 합계 열 번호 | 월별 열 |
|---|---|---:|---|---|
| 매출 | 매출_세부_IFRS15後 | 5 | 8, 22, 36, 50 | 해당 연간 열 + 1~12 |
| 비용 | OpEx_세부_IFRS15後 | 6 | 17, 31, 45, 59, 73 | 해당 연간 열 + 1~12 |

기간은 prior, plan, outlook, next_plan이며 비용에는 next_projection이 추가된다. 파일마다 117개 합계 그룹이 있다. 각 그룹의 행 수는 manifest의 records를 사용한다. 원본의 상단 SUBTOTAL은 평가에 쓰지 않으므로 마스터 뒤쪽 상세 행도 분모에 포함된다. 정답 금액은 생성 계약의 천원에서 원으로 1000배 변환한다.

필수 셀 누락·중복·미확정·비금액 결과가 하나라도 있으면 해당 그룹은 incomplete다. 전부 들어오고 Decimal 합계가 일치해야 matching이다. 부분 매핑이어도 확정된 금액 자체는 합계 평가에 사용할 수 있으나 mapping 상태는 따로 기록한다. 의미가 틀린데 우연히 합계가 같은 경우는 이 평가만으로 탐지할 수 없다. 양식 변경 데이터에는 이 고정 위치 정답 계약을 재사용하지 않는다.

### 선택적 독립 셀 정답

`--truth /path/to/independent-truth.json`으로 JSON 배열 또는 `{"records": [...]}`를 제공하면 셀별 의미·금액 정확도도 계산한다. 없는 경우 정확도는 0이나 100% 대신 `null / not_scored`다. 예시는 파일 생성 명령이 아니라 계약 설명이다.

```json
[
  {
    "source_file": "example.xlsx",
    "sheet": "매출_세부_IFRS15後",
    "cell": "I5",
    "organization_code": "F10",
    "year": 2025,
    "month": 1,
    "aggregation": "month",
    "scenario": "actual",
    "normalized_value": "1000",
    "item_name": "독립 검토한 항목명",
    "source_unit": "천원"
  }
]
```

source_file은 파일 기본명이다. item_code, item_name, source_unit은 선택 필드이며 제공하면 비교한다. 필수 의미 필드는 조직 코드·연도·월·집계 구분·시나리오다. 출처는 파일명+시트+셀로 연결한다. 금액은 유한 Decimal로 비교하고 누락과 중복도 정답 분모에 포함한다. 전체 정답 판정에는 mapped와 confirmed가 필요하다. 미해결 의미에 올바른 금액만 있으면 value_exact로만 별도 보고한다. 정답 범위 밖 출력은 extra로 보고하며 자동으로 오류라고 단정하지 않는다.

정답은 독립적으로 검토해야 한다. 어느 한 파서의 결과를 그대로 정답으로 복사하면 해당 파서에 유리한 평가가 된다. 파일 일부에만 정답이 있을 때는 해당 파일·셀만 평가하며 셀 정답 수를 함께 표시한다.

## 보고서 해석

- `completed`: 추출·해석·출처 검증 단계의 연산 완료. 의미 정답 여부와는 다르다.
- `validation_passed`와 `validation.*.passed`: 원본 추출·출처 검사의 결과. 알려진 메모 불일치를 숨기지 않는다.
- `timings.total_seconds`: 원본 해시, 추출, 독립 원본 검사, 해석, 출처 검사 포함. 독립 정답 채점과 보고서 직렬화는 제외한다. 파일 저장을 하지 않는 두 엔진을 같은 조건에서 비교한다.
- `summary`: 방법·반복·조직/마스터 구분별 합계 시간, 파일별 중앙값·최대값, 금액 합계 일치율, 선택적 셀 정확도, 실제 호출 수와 관측 토큰.
- `source_consistent_across_trials`: 같은 파일의 엔진·반복 간 원본 동일성. false인 실험으로 성능 우열을 발표하지 않는다.
- `inference`: 현재 파일의 이벤트와 누적값 차이. API 요청 수와 HTTP 재전송 수를 구분한다. 원래 클라이언트에서 응답 파싱 실패 이벤트가 빠지는 경우도 사용량 미확인 호출로 기록한다.
- `conditions.implementation_sha256`: 두 파서와 평가기의 Python 소스 해시. 모델·추론 강도·review·캐시 조건과 함께 재현 조건으로 보관한다.

현재 기본 데이터의 `예산코드!A178 COMMENT_MISMATCH`는 검증 결과에 남기고 해석은 계속한다. CLI는 검증 실패가 있으면 결과를 출력하되 종료 코드 2를 반환한다. 현재 AI의 수식 비율 기반 OpEx 생략도 그대로 기록하고, 해당 필수 금액은 평가 분모에서 제거하지 않는다. 두 문제의 수정 전후를 비교하려면 별도 실험으로 구분한다.

비교 환경 연결 확인에서 실제 `01_F10_사업부1_재무제표.xlsx`와 `18_전체_마스터.xlsx`의 규칙 파서는 각각 비용 65개 그룹만 일치했다. 매출 헤더에 `사업부코드`가 없어서 기존 `mapping._layout`의 매출 조건을 통과하지 못하고, 매출 값이 단위 없는 일반 숫자로 남았다. 따라서 매출 52개 그룹은 금액으로 확정되지 않은 결과로 집계한다. 평가 전용 원본 금액 합산에서는 두 파일의 117개 그룹 모두 manifest와 일치하여 정답 범위 문제와 구분했다. 이것은 두 파일의 연결 확인 결과이며 전체 데이터 또는 AI와의 성능 비교 결과는 아니다. 비교 환경은 이 규칙을 수정하지 않는다.

## 검증

```bash
PYTHONPATH=parser/src:parser_ai/src python3 -B -m unittest discover \
  -s parser/tests -p 'test_benchmark_*.py' -v
```

합성 OOXML, 독립 정답, 대체 API 응답으로 실제 추출·두 해석 경로·검증·채점을 연결한다. 누락·중복·금액 오류·잘못된 의미·AI 부분 실패·캐시 재사용·정답 격리·원본 변경·출력 경로 보호를 검사한다. 실제 API의 정확도·속도 측정이나 전체 서비스 통합 검증을 대신하지 않는다.

## 함수 연결

`compare.py → cli.main → load_dataset/load_truth → plan_comparison 또는 run_comparison → run_rule_adapter/run_ai_adapter → score_manifest/score_records → summarize_trials` 순서다. 기본값은 콘솔 출력이며 save_report는 명시적 저장 옵션에서만 호출한다.
