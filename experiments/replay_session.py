"""원래 해석 계획을 재생하는 비교 실행기. 새 모델 추론은 수행하지 않는다."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "parser/src"), str(REPO / "parser_ai/src")]
from session_plans import organization_plans, reference_responses, master_responses

from excel_parser.benchmark.runner import load_dataset,run_comparison
from excel_parser_ai.client import ResponsesClient,ModelError

def deny_writes(event: str, args: tuple) -> None:
    """재생 중 파일 쓰기·네트워크·외부 프로세스 실행을 차단한다.

    Args:
        event: Python 감사 이벤트 이름.
        args: 해당 이벤트의 인자.
    Returns:
        없음.
    Raises:
        RuntimeError: 차단 대상 이벤트가 발생한 경우.
    Side Effects:
        해당 작업을 예외로 중단한다.
    """
    if event == "open" and (isinstance(args[1],str) and any(c in args[1] for c in "wax+") or isinstance(args[2],int) and args[2]&0x0603):
        raise RuntimeError("session comparison: file write blocked")
    if event in {"os.mkdir","os.remove","os.rename","socket.connect","subprocess.Popen"}:
        raise RuntimeError("session comparison: mutation/network/subprocess blocked")

current_filename = None
replay_requests = []
def progress(message: str) -> None:
    """현재 파일을 기억하고 진행 상태를 콘솔에 출력한다.

    Args:
        message: 비교 실행기의 엔진·파일 진행 메시지.
    Returns:
        없음.
    Raises:
        IndexError: 진행 메시지 계약이 달라진 경우.
    Side Effects:
        current_filename을 갱신하고 콘솔에 출력한다.
    """
    global current_filename
    current_filename = message.split(": ",1)[1]
    print(message,flush=True)

def transport(body: dict) -> dict:
    """파일·시트·블록에 해당하는 고정 계획을 API 응답 형식으로 재생한다.

    Args:
        body: 원래 AI 클라이언트가 생성한 구조 요청.
    Returns:
        미리 작성한 구조 계획을 포함한 응답 객체.
    Raises:
        ModelError: 계획에 대한 원본 근거 검증이 실패한 경우.
        KeyError: 해당 입력에 대한 고정 계획이 없는 경우.
    Side Effects:
        replay_requests에 재생 요청을 기록하며 네트워크에는 접근하지 않는다.
    """
    context=json.loads(body["input"][1]["content"])
    task=body["text"]["format"]["name"]
    if "validation_errors" in context:
        print("PLAN_VALIDATION_ERRORS "+json.dumps({"file":current_filename,"errors":context["validation_errors"]},ensure_ascii=False),flush=True)
        raise ModelError("Authored session plan needs correction after source validation")
    source=context.get("source",context)
    sheet=source["sheet"]
    bounds=tuple(source["declared_row_range"]) if source.get("declared_row_range") else None
    if sheet == "예산코드":
        response=reference_responses[bounds]
    elif current_filename.startswith("18_"):
        response=master_responses[(sheet,bounds)]
    else:
        response={"plan":organization_plans[current_filename]}
    replay_requests.append({"file":current_filename,"sheet":sheet,"bounds":bounds,"task":task})
    return {"status":"completed","model":"codex-session-authored-plan-replay","usage":{},
            "output":[{"content":[{"type":"output_text","text":json.dumps(response,ensure_ascii=False)}]}]}


def main() -> int:
    """별도 기본 더미 데이터로 당시 고정 계획과 두 파서를 재생한다.

    Args:
        없음. 명령행에서 dataset 경로를 읽는다.
    Returns:
        재생 완료 시 0. 금액·추출 검증 통과 여부는 보고서의 개별 필드로 확인한다.
    Raises:
        SystemExit: 도움말 또는 잘못된 명령행 입력.
        OSError: 원본 데이터 읽기 실패.
        ValueError: manifest 또는 입력 계약 불일치.
        ModelError: 고정 계획의 검증 실패.
    Side Effects:
        읽기·콘솔 출력을 수행하며 쓰기·네트워크·외부 프로세스를 차단한다.
        감사 훅은 현재 프로세스 종료까지 유지된다.
    """
    parser = argparse.ArgumentParser(description="API 없이 2026-11-12 세션의 고정 계획을 재생")
    parser.add_argument("dataset", type=Path, help="기본 18개 Excel과 manifest_baseline.json이 있는 폴더")
    args = parser.parse_args()
    sys.addaudithook(deny_writes)
    files,manifest,_=load_dataset(args.dataset,include_master=True)
    client=ResponsesClient(REPO / "parser_ai/cache",response_cache={},transport=transport,
                           model="codex-session-authored-plan-replay",effort="session_not_controlled",max_calls=300)
    report=run_comparison(files,manifest,engines=("rule","ai"),repeats=1,cache_mode="cold",
                          review=False,allow_api=True,client=client,progress=progress)
    report["method"]="session_authored_structure_plans_replayed_into_original_ai_engine"
    report["model_generation_seconds"]=None
    report["actual_external_api_calls"]=0
    report["replay_requests"]=replay_requests
    report["limitations"].extend(["대화 모델이 원본을 추가 탐색하여 작성한 계획을 재생한 품질 실험이다.",
        "원래 API 모델의 정확도와 동일하지 않으며, 시간은 모델의 계획 작성 및 도구 왕복을 제외한다.",
        "독립 review API 단계는 off이며 모델별 토큰·청구 비용을 관측하지 않았다."])
    for row in report["trials"]:
        checks=row["financial_aggregate_evaluation"]
        by_section={}
        for detail in checks["details"]:
            group=by_section.setdefault(detail["section"],Counter())
            group["groups_in_details"]+=1
            group["matching_groups_in_details"]+=int(detail["matches"])
            group["unresolved_cells_in_details"]+=detail["unresolved_cells"]
        row["section_detail_counts"]={k:dict(v) for k,v in by_section.items()}
        row["failed_group_samples"]=[d for d in checks["details"] if not d["matches"]][:5]
        checks.pop("details",None)
        row["inference"].pop("events",None)
        row["inference"]["plan_replay_requests"]=row["inference"].get("logical_attempted_calls",0)
        row["inference"]["api_calls"]=0
    print("RESULT_JSON "+json.dumps(report,ensure_ascii=False,default=str),flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
