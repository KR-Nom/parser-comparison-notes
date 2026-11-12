# 재무 파서 비교: 코드와 검증 기록

룰베이스 파서, LLM 구조 해석 파서, 공통 비교 도구와 검증 문서를 모은 저장소입니다. 기존 작업 폴더의 파서 구현을 함께 옮겼으며, 운영 파서의 매핑 규칙이나 AI 처리 정책을 이번 게시 작업에서 수정하지 않았습니다.

## 구성

| 경로 | 내용 |
|---|---|
| [parser/](parser/) | 원본 OOXML 추출, 룰베이스 의미 매핑, 숫자 정규화, 출처 검증 |
| [parser_ai/](parser_ai/) | LLM 구조 계획, 블록 분할, 응답 검증, 계획 적용, 기존 셀별 엔진 |
| [parser/compare.py](parser/compare.py) | 독립 실행·시간 측정·금액 합계·선택적 셀 정답 평가 |
| [experiments/](experiments/) | 당시 대화 모델이 작성한 고정 계획과 API 없는 재생 실행기 |
| [정답셋 문서](docs/parser-comparison-ground-truth.md) | 정답 출처, 117개 그룹 채점 방식, 의미 정확도와의 차이 |
| [현재 검증·재현 범위](docs/source-snapshot.md) | 코드 출처, 테스트, 재현 조건과 한계 |

`parser/`와 `parser_ai/`는 형제 폴더 관계를 유지해야 합니다. Python 3.11 이상과 표준 라이브러리로 소스 체크아웃에서 실행할 수 있습니다. 입력 Excel, 기본 더미 manifest, 실제 API 키는 별도로 준비해야 합니다.

## 데이터·API 없이 테스트

저장소 루트에서 실행합니다.

```bash
PYTHONPATH=parser/src:parser_ai/src python3 -B -m unittest discover -s parser/tests -v
PYTHONPATH=parser/src:parser_ai/src python3 -B -m unittest discover -s parser_ai/tests -v
python3 -B parser/compare.py --help
```

규칙·비교 테스트는 85개 중 82개 통과, 외부 데이터가 필요한 3개 건너뜀입니다. AI mock 테스트는 87개 모두 통과했습니다. mock 테스트 통과는 실제 모델 정확도를 보증하지 않습니다.

## 룰베이스와 실제 AI API 비교

`/path/to/Data/dummy`는 기본 조직 17개, 마스터 1개, `manifest_baseline.json`이 있는 별도 폴더로 바꿉니다.

```bash
# API 없이 구조 요청 계획만 확인
python3 -B parser/compare.py plan /path/to/Data/dummy --include-master --review off

# 룰베이스만 실행
python3 -B parser/compare.py run /path/to/Data/dummy --engines rule --include-master

# OPENAI_API_KEY를 설정한 뒤 실제 API를 사용하는 비교
# YOUR_MODEL_ID에는 해당 계정에서 사용할 모델을 지정
python3 -B parser/compare.py run /path/to/Data/dummy --engines both --include-master --allow-api --review off --model YOUR_MODEL_ID
```

비교 CLI는 기본적으로 메모리·콘솔만 사용합니다. `--write-output`을 추가하면 비교 요약 JSON·CSV를 저장합니다. AI 일반 CLI인 `parser_ai/run.py prepare`와 `run`은 결과 폴더를 생성하는 별도 경로이므로 구분해야 합니다. 기존 `.command` 파일은 고정된 로컬 더미 경로를 사용합니다.

비교 도구는 추출 검증 실패가 남으면 보고서를 출력해도 종료 코드 2를 반환할 수 있습니다. 이번 기본 더미에서는 `예산코드!A178 COMMENT_MISMATCH`가 관찰됐습니다. 자세한 명령과 계약은 [비교 환경 문서](parser/docs/development/parser-comparison.md)를 참고하세요.

## 보고서의 AI 계획 재생 결과 확인

```bash
python3 -B experiments/replay_session.py /path/to/Data/dummy
```

이 실행기는 `experiments/session_plans.py`의 **이미 작성된 구조 해석 계획**을 원래 AI 엔진에 전달합니다. 모델 추론과 실제 HTTP 요청을 하지 않으며 파일 쓰기도 차단합니다. 출력은 진행 메시지와 `RESULT_JSON`으로 시작하는 비교 보고서입니다.

재생은 기본 18개 파일의 파일명·시트·행 범위에 맞춰져 있습니다. 새 양식에 대응하는 일반 AI 실행기가 아닙니다. 원본 추가 탐색으로 작성한 계획이므로 원래 API 호출과 입력 정보가 완전히 같지 않으며, 시간에는 계획 생성 과정이 포함되지 않습니다. 종료 코드 0은 재생 완료를 뜻하고 금액·추출·출처 검증 성공 여부는 보고서의 개별 필드로 확인합니다.

## 결과 해석

- 현재 결과는 **금액 집계 일치율**입니다. 셀별 조직·항목·기간·시나리오의 의미 정확도는 독립 정답이 없어 미측정입니다.
- 마스터는 조직 파일을 합친 자료로, 독립적인 추가 표본이 아닙니다.
- AI 조직 비용 시트의 미해석은 수식 비율에 따른 사전 제외 조건과 관련됩니다. 이를 모델이 비용 의미를 이해하지 못한 결과로 단정할 수 없습니다.
- 원본 데이터·키·캐시·실행 결과 파일은 포함하지 않습니다. 고정 구조 계획은 재생용 코드로 포함합니다.
- 기존 AI 문서의 과거 API 타임아웃·요청량 측정과 이번 재생 실험을 분리해서 읽어야 합니다.

기준 확인일: 2026-11-12
