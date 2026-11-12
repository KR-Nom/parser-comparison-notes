"""각 파서의 실제 추출·해석·검증을 독립 실행하는 메모리 벤치마크 경계."""

from collections.abc import Callable
from pathlib import Path
from time import perf_counter
from typing import Any

from ..extraction import extract_workbook, file_fingerprint
from ..mapping import map_workbook
from ..validation import validate_extraction, validate_provenance


def _inference_delta(client: Any, offset: int, calls: int, posts: int) -> dict:
    """공유 클라이언트에서 현재 파일 실행에 해당하는 사용량만 분리한다.

    Args:
        client: AI 클라이언트 또는 규칙 파서를 뜻하는 None.
        offset: 실행 전 이벤트 목록 길이.
        calls: 실행 전 논리 호출 수.
        posts: 실행 전 HTTP 전송 시도 수.
    Returns:
        현재 파일의 이벤트, 호출, 캐시 적중, 토큰 사용량 사전.
    Raises:
        없음.
    Side Effects:
        클라이언트의 메모리 상태만 조회하며 변경하지 않는다.
    """
    events = list(client.events[offset:]) if client is not None else []
    attempted_calls = client.call_count - calls if client is not None else 0
    uncached_events = [event for event in events if not event["cached"]]
    unlogged_calls = max(0, attempted_calls - len(uncached_events))
    return {
        "events": events,
        "logical_attempted_calls": attempted_calls,
        "http_post_attempts": client.http_post_attempts - posts if client is not None else 0,
        "api_calls": len(uncached_events) + unlogged_calls,
        "cache_hits": sum(event["cached"] for event in events),
        "failed_calls": sum(event.get("failed", False) for event in events) + unlogged_calls,
        "unlogged_attempted_calls": unlogged_calls,
        "usage_missing_calls": sum(not event.get("usage") for event in uncached_events) + unlogged_calls,
        "input_tokens": sum(event.get("usage", {}).get("input_tokens", 0) for event in events),
        "output_tokens": sum(event.get("usage", {}).get("output_tokens", 0) for event in events),
        "cost_usd": None,
        "cost_status": "not_estimated",
        "model": getattr(client, "model", None),
        "reasoning_effort": getattr(client, "effort", None),
    }


def _run_adapter(
    path: Path,
    *,
    parser_name: str,
    interpret: Callable[[dict], tuple[dict, dict]],
    root: Path | None = None,
    client: Any = None,
) -> dict:
    """원본을 새로 읽어 각 단계의 시간과 실패를 결과 사전에 남긴다.

    Args:
        path: 읽기 전용 원본 Excel 경로.
        parser_name: 결과에 남길 파서 식별자.
        interpret: 추출 결과를 받아 mapped, semantic을 반환하는 함수.
        root: 원본 출처 경로를 상대 표시할 선택 루트.
        client: 현재 파일의 AI 호출 차이를 기록할 선택 클라이언트.
    Returns:
        원본·매핑·검증·시간·오류·사용량을 포함하는 메모리 실행 결과.
        completed는 연산·해석 완료와 원본 보존 여부이다. 추출·출처 검증의
        통과 여부는 validation_passed로 별도 보고한다.
    Raises:
        KeyboardInterrupt: 사용자 중단을 호출자에게 그대로 전파한다.
    Side Effects:
        원본을 추출·독립 검증·해시 계산을 위해 읽는다. interpret가 명시적으로
        허용된 AI 호출을 수행할 수 있으나 이 함수는 파일을 생성하지 않는다.
    """
    source = Path(path).expanduser().resolve()
    events_before = len(client.events) if client is not None else 0
    calls_before = client.call_count if client is not None else 0
    posts_before = client.http_post_attempts if client is not None else 0
    result = {
        "parser": parser_name,
        "source": {"path": str(source)},
        "workbook": None,
        "mapped": None,
        "semantic": {},
        "validation": {"extraction": None, "provenance": None},
        "source_integrity": {"sha256_before": None, "sha256_after": None, "unchanged": False},
        "timings": {},
        "errors": [],
        "completed": False,
        "validation_passed": False,
    }
    started = perf_counter()
    stage = "source_hash_before"
    stage_started = started
    try:
        result["source_integrity"]["sha256_before"] = file_fingerprint(source)
        result["timings"][stage + "_seconds"] = perf_counter() - stage_started
        stage = "extraction"
        stage_started = perf_counter()
        workbook = extract_workbook(source, root=root)
        result["workbook"] = workbook
        result["source"] = workbook["file"]
        result["timings"][stage + "_seconds"] = perf_counter() - stage_started
        stage = "extraction_validation"
        stage_started = perf_counter()
        raw_check = validate_extraction(source, workbook)
        result["validation"]["extraction"] = raw_check
        result["timings"][stage + "_seconds"] = perf_counter() - stage_started
        stage = "interpretation"
        stage_started = perf_counter()
        mapped, semantic = interpret(workbook)
        result["mapped"] = mapped
        result["semantic"] = semantic
        result["timings"][stage + "_seconds"] = perf_counter() - stage_started
        stage = "provenance_validation"
        stage_started = perf_counter()
        result["validation"]["provenance"] = validate_provenance(workbook, mapped)
        result["timings"][stage + "_seconds"] = perf_counter() - stage_started
        if parser_name == "ai":
            failures = semantic.get("failures", [])
            result["errors"].extend({"stage": "interpretation", **failure} for failure in failures)
            if len(semantic.get("structure_plans", [])) != len(workbook["sheets"]) and not failures:
                result["errors"].append({
                    "stage": "interpretation", "error_type": "IncompleteStructure",
                    "message": "모든 원본 시트의 구조 해석이 완료되지 않았습니다.",
                })
    except Exception as error:
        result["timings"].setdefault(stage + "_seconds", perf_counter() - stage_started)
        result["errors"].append({"stage": stage, "error_type": type(error).__name__, "message": str(error)})
    finally:
        hash_started = perf_counter()
        try:
            after = file_fingerprint(source)
            integrity = result["source_integrity"]
            integrity["sha256_after"] = after
            integrity["unchanged"] = integrity["sha256_before"] is not None and integrity["sha256_before"] == after
            if integrity["sha256_before"] is not None and not integrity["unchanged"]:
                result["errors"].append({
                    "stage": "source_integrity", "error_type": "SourceChanged",
                    "message": "처리 전후 원본 해시가 다릅니다.",
                })
        except Exception as error:
            result["errors"].append({"stage": "source_hash_after", "error_type": type(error).__name__, "message": str(error)})
        result["timings"]["source_hash_after_seconds"] = perf_counter() - hash_started
        result["timings"]["total_seconds"] = perf_counter() - started
        result["inference"] = _inference_delta(client, events_before, calls_before, posts_before)
    result["completed"] = bool(
        not result["errors"]
        and result["source_integrity"]["unchanged"]
        and result["mapped"] is not None
        and result["validation"]["provenance"] is not None
    )
    result["validation_passed"] = bool(
        result["source_integrity"]["unchanged"]
        and (result["validation"]["extraction"] or {}).get("passed")
        and (result["validation"]["provenance"] or {}).get("passed")
    )
    return result


def run_rule_adapter(path: Path, *, metadata: dict | None = None, root: Path | None = None) -> dict:
    """규칙 파서를 저장 없이 새 추출부터 실행한다.

    Args:
        path: 읽기 전용 Excel 입력 경로.
        metadata: 같은 파일에 적용할 명시적인 제출·조직 메타데이터.
        root: 출처 경로를 상대 표시할 선택 루트.
    Returns:
        실제 규칙 매핑과 독립 검증 결과 및 단계별 시간.
    Raises:
        KeyboardInterrupt: 사용자가 실행을 중단한 경우.
    Side Effects:
        원본 파일을 읽으며 파일 생성과 네트워크 호출은 하지 않는다.
    """
    return _run_adapter(
        path, parser_name="rule", root=root,
        interpret=lambda workbook: (map_workbook(workbook, metadata), {}),
    )


def run_ai_adapter(
    path: Path,
    *,
    client: Any,
    allow_api: bool = False,
    template_cache: dict | None = None,
    review: bool = True,
    max_exception_calls: int = 0,
    metadata: dict | None = None,
    root: Path | None = None,
) -> dict:
    """명시적으로 허용된 AI 구조 파서를 메모리 캐시만 사용해 실행한다.

    Args:
        path: 읽기 전용 Excel 입력 경로.
        client: response_cache 사전을 사용하는 ResponsesClient 호환 객체.
        allow_api: 실제 또는 대체 전송을 포함한 AI 실행을 명시적으로 허용할지.
        template_cache: 실행 간 공유할 메모리 구조 템플릿. None이면 새 사전.
        review: 독립 의미 검토를 추가할지 여부.
        max_exception_calls: 기존 구조 엔진의 예외 해석 호출 상한.
        metadata: 같은 파일에 적용할 명시적인 제출·조직 메타데이터.
        root: 출처 경로를 상대 표시할 선택 루트.
    Returns:
        실제 AI 구조 해석·원본 적용·독립 검증과 현재 파일의 호출 사용량.
    Raises:
        ValueError: AI 실행 미허용, 디스크 캐시 클라이언트 또는 잘못된 설정.
        ImportError: excel_parser_ai 패키지가 import 경로에 없는 경우.
        KeyboardInterrupt: 사용자가 실행을 중단한 경우.
    Side Effects:
        원본을 읽고 명시적으로 허용된 모델을 호출할 수 있다. 클라이언트 및
        템플릿의 메모리 캐시는 갱신하지만 파일과 폴더는 만들지 않는다.
    """
    if not allow_api:
        raise ValueError("AI 벤치마크는 allow_api=True를 명시해야 실행합니다.")
    if not isinstance(getattr(client, "response_cache", None), dict):
        raise ValueError("벤치마크 AI 클라이언트는 메모리 response_cache 사전이 필요합니다.")
    if template_cache is not None and not isinstance(template_cache, dict):
        raise ValueError("template_cache는 메모리 사전이어야 합니다.")
    if max_exception_calls < 0:
        raise ValueError("max_exception_calls는 0 이상이어야 합니다.")
    from excel_parser_ai.structured import infer_structure

    templates = {} if template_cache is None else template_cache
    result = _run_adapter(
        path, parser_name="ai", root=root, client=client,
        interpret=lambda workbook: infer_structure(
            workbook, client, review=review, max_exception_calls=max_exception_calls,
            metadata=metadata, template_cache=templates,
        ),
    )
    result["inference"]["semantic_review_enabled"] = review
    result["inference"]["max_exception_calls"] = max_exception_calls
    return result
