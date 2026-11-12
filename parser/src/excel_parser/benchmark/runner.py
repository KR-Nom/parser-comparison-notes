"""같은 파일과 검증 단계를 사용하는 메모리 전용 비교 실행기."""

from __future__ import annotations

import hashlib
import json
import platform
import statistics
import time
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from excel_parser.extraction import extract_workbook, file_fingerprint

from .adapters import run_ai_adapter, run_rule_adapter
from .evaluation import score_records
from .manifest import score_manifest


def load_dataset(
    dataset: Path, manifest_path: Path | None = None, *,
    include_master: bool = False, max_files: int | None = None,
) -> tuple[list[Path], dict, Path]:
    """독립 manifest에 명시된 기본 더미 파일만 선택한다.

    Args:
        dataset: 입력 파일 디렉터리.
        manifest_path: 정답 manifest 경로. 생략하면 dataset 안의 기본 이름.
        include_master: 마스터를 별도 입력으로 포함할지 여부.
        max_files: 정렬된 선택 파일 수 상한.
    Returns:
        선택 파일 목록, manifest 객체, manifest의 절대 경로.
    Raises:
        ValueError: 다른 데이터 계약, 중복 이름 또는 잘못된 선택 조건.
        OSError: 입력이나 manifest를 읽을 수 없는 경우.
    Side Effects:
        manifest와 파일 존재 여부만 읽고 파일을 생성하지 않는다.
    """
    dataset = Path(dataset).expanduser().resolve()
    manifest_path = (manifest_path or dataset / "manifest_baseline.json").resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"), parse_float=Decimal)
    if not isinstance(manifest, dict):
        raise ValueError("manifest는 JSON 객체여야 합니다")
    if manifest.get("version") != "baseline_strict_native_input_only_v1":
        raise ValueError("지원하는 평가 계약은 baseline_strict_native_input_only_v1입니다")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise ValueError("manifest.files는 비어 있지 않은 목록이어야 합니다")
    if max_files is not None and max_files < 1:
        raise ValueError("max_files는 양수여야 합니다")
    names = set()
    selected = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("manifest.files의 각 항목은 객체여야 합니다")
        name = entry.get("filename")
        if not isinstance(name, str) or Path(name).name != name or "\\" in name:
            raise ValueError("manifest.filename은 경로가 아닌 파일명이어야 합니다")
        if name in names or Path(name).suffix.lower() != ".xlsx":
            raise ValueError("중복 파일명 또는 지원하지 않는 확장자입니다")
        names.add(name)
        if not isinstance(entry.get("is_master"), bool):
            raise ValueError("manifest.is_master는 명시적인 불리언이어야 합니다")
        if entry["is_master"] and not include_master:
            continue
        path = (dataset / name).resolve()
        if not path.is_relative_to(dataset) or not path.is_file():
            raise ValueError("선택한 입력이 데이터 폴더 안의 파일이 아닙니다: " + name)
        selected.append(path)
    selected = sorted(selected)[:max_files]
    if not selected:
        raise ValueError("비교할 입력 파일이 없습니다")
    return selected, manifest, manifest_path


def load_truth(path: Path | None, files: list[Path]) -> list[dict] | None:
    """선택 파일에 해당하는 독립 셀 정답을 읽는다.

    Args:
        path: JSON 목록 또는 records 목록을 가진 JSON 객체 경로.
        files: 이번에 선택한 원본 파일 목록.
    Returns:
        선택 파일의 정답 행 목록. 경로가 없으면 None.
    Raises:
        ValueError: 정답 형식이나 필수 필드가 잘못된 경우.
        OSError: 정답 파일을 읽을 수 없는 경우.
    Side Effects:
        지정한 정답 파일만 읽는다. 파서에는 전달하지 않는다.
    """
    if path is None:
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"), parse_float=Decimal)
    rows = data.get("records") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError("셀 정답은 목록 또는 records 목록을 가진 객체여야 합니다")
    score_records([], rows)
    if any(Path(row["source_file"]).name != row["source_file"] or "\\" in row["source_file"] for row in rows):
        raise ValueError("셀 정답 source_file은 경로가 아닌 파일 기본명이어야 합니다")
    selected = {p.name for p in files}
    return [row for row in rows if row["source_file"] in selected]


def plan_comparison(files: list[Path], *, review: bool = True) -> dict:
    """실제 구조 분기에서 캐시 재사용 전 요청 작업량을 계산한다.

    Args:
        files: 독립적으로 읽을 입력 파일 목록.
        review: 구조 작업마다 검토를 추가하는지 여부.
    Returns:
        파일별 구조 작업과 생략 내역. 모델 성능 측정값은 포함하지 않는다.
    Raises:
        OSError: 입력 파일을 읽지 못한 경우.
        ValueError: 지원하지 않는 Excel 형식인 경우.
    Side Effects:
        입력을 읽기만 하며 API와 디스크 캐시에 접근하지 않는다.
    """
    from excel_parser_ai.structured import (
        FORMULA_SKIP_RATIO, LARGE_SHEET_ROW_THRESHOLD,
        _formula_ratio, _row_blocks,
    )

    results = []
    for path in files:
        workbook = extract_workbook(path)
        sheets = []
        for sheet in workbook["sheets"]:
            cells = [c for c in sheet["cells"] if c.get("has_content", True)]
            candidates = [c for c in cells if c["data_type"] == "number" or c.get("formula")]
            numbers = sorted({c["row"] for c in candidates})
            ratio = _formula_ratio(sheet)
            reason = "no_numeric_candidates" if not candidates else (
                "formula_ratio_skip" if ratio >= FORMULA_SKIP_RATIO else None
            )
            jobs = 0 if reason else (len(_row_blocks(numbers))
                                     if len(numbers) > LARGE_SHEET_ROW_THRESHOLD else 1)
            sheets.append({"sheet": sheet["name"], "content_cells": len(cells),
                           "candidate_rows": len(numbers), "formula_ratio": ratio,
                           "base_jobs": jobs, "skip_reason": reason,
                           "skipped_numeric_cells": sum(not c.get("formula") for c in candidates)
                           if reason else 0})
        results.append({"file": path.name, "sha256": file_fingerprint(path),
                        "sheets": sheets, "base_jobs": sum(s["base_jobs"] for s in sheets)})
    jobs = sum(r["base_jobs"] for r in results)
    return {"mode": "plan", "api_executed": False, "files": results,
            "base_jobs": jobs, "logical_calls_without_reuse_or_repair": jobs * (2 if review else 1),
            "review": review, "assumptions": ["응답·구조 캐시 재사용 없음",
            "모든 작업이 첫 응답에 검증 통과", "repair·HTTP 재시도·실패에 따른 중단 제외"]}


def _compact_validation(value: dict | None) -> dict:
    """검증 결과에서 대형 원본과 과도한 상세 목록을 제외한다.

    Args:
        value: 검증 보고서 또는 None.
    Returns:
        검사 결과, 오류 개수와 처음 10개 오류를 가진 객체.
    Raises:
        없음.
    Side Effects:
        없음. 원래 객체를 변경하지 않는다.
    """
    if not value:
        return {"passed": None, "issue_count": None, "issues": []}
    issues = value.get("issues", [])
    return {**{k: v for k, v in value.items() if k not in {"sheets", "issues", "file"}},
            "issue_count": len(issues), "issues": issues[:10]}


def _summarize_result(result: dict, entry: dict, truth: list[dict] | None) -> dict:
    """파서 결과를 독립 평가하고 원본 객체를 제외한 보고서를 만든다.

    Args:
        result: 공통 어댑터 결과.
        entry: 해당 파일의 독립 manifest 정답.
        truth: 선택적 독립 셀 정답.
    Returns:
        측정·검증·금액 집계 및 선택적 의미 정확도 보고서.
    Raises:
        ValueError: 독립 정답 계약이 잘못된 경우.
    Side Effects:
        없음. 정답을 파서나 모델에 전달하지 않는다.
    """
    mapped = result.get("mapped") or {}
    records = mapped.get("records", [])
    semantic = result.get("semantic") or {}
    validation = result.get("validation") or {}
    report = {k: v for k, v in result.items() if k not in {"workbook", "mapped", "semantic", "validation"}}
    report["record_counts"] = {"total": len(records),
                               "mapping": dict(Counter(r["mapping"]["status"] for r in records)),
                               "value": dict(Counter(r["value"]["status"] for r in records))}
    report["validation"] = {name: _compact_validation(validation.get(name))
                            for name in ("extraction", "provenance")}
    report["semantic_status"] = {"skipped_sheets": semantic.get("skipped_sheets", []),
                                 "failures": semantic.get("failures", []),
                                 "reused_sheets": semantic.get("reused_sheets", [])}
    report["financial_aggregate_evaluation"] = score_manifest(records, entry)
    selected_truth = None if truth is None else [r for r in truth if r["source_file"] == entry["filename"]]
    report["cell_evaluation"] = score_records(records, selected_truth) if selected_truth else {
        "accuracy": None, "status": "not_scored", "reason": "이 파일의 독립 셀 정답이 제공되지 않음"}
    return report


def summarize_trials(trials: list[dict]) -> list[dict]:
    """방법·반복·마스터 구분별로 전체 처리 시간과 평가 수를 집계한다.

    Args:
        trials: 원본 객체를 제거한 파일별 실행 결과 목록.
    Returns:
        동일 분모를 유지한 반복 실행별 요약 목록.
    Raises:
        KeyError: 어댑터 결과의 필수 측정 필드가 없는 경우.
    Side Effects:
        없음.
    """
    groups: dict[tuple, list] = {}
    for row in trials:
        key = (row["parser"], row["repetition"], row["input_role"])
        groups.setdefault(key, []).append(row)
    summaries = []
    for (parser, repetition, role), rows in groups.items():
        times = [r["timings"]["total_seconds"] for r in rows]
        checks = [r["financial_aggregate_evaluation"] for r in rows]
        expected = sum(c["groups_total"] for c in checks)
        matched = sum(c["matching_groups"] for c in checks)
        expected_cells = sum(r["cell_evaluation"].get("total_expected", 0) for r in rows)
        correct_cells = sum(r["cell_evaluation"].get("exact_match", 0) for r in rows)
        summaries.append({"parser": parser, "repetition": repetition, "input_role": role,
                          "files": len(rows), "completed_files": sum(bool(r["completed"]) for r in rows),
                          "validation_passed_files": sum(r["validation"]["extraction"]["passed"] is True
                                                           and r["validation"]["provenance"]["passed"] is True for r in rows),
                          "total_seconds": sum(times), "median_file_seconds": statistics.median(times),
                          "max_file_seconds": max(times), "financial_groups": expected,
                          "matching_financial_groups": matched,
                          "financial_group_match_rate": matched / expected if expected else None,
                          "cell_truth_count": expected_cells, "cell_exact_count": correct_cells,
                          "cell_accuracy": correct_cells / expected_cells if expected_cells else None,
                          "source_consistent_across_trials": all(r.get("source_consistent_across_trials", False) for r in rows),
                          "logical_calls": sum(r["inference"].get("logical_attempted_calls", 0) for r in rows),
                          "http_post_attempts": sum(r["inference"].get("http_post_attempts", 0) for r in rows),
                          "input_tokens_observed": sum(r["inference"].get("input_tokens", 0) for r in rows),
                          "output_tokens_observed": sum(r["inference"].get("output_tokens", 0) for r in rows),
                          "usage_missing_calls": sum(r["inference"].get("usage_missing_calls", 0) for r in rows),
                          "cost_usd": None})
    return summaries


def implementation_fingerprint() -> str:
    """두 파서와 평가기 소스의 재현 식별 해시를 만든다.

    Args:
        없음.
    Returns:
        상대 경로와 Python 소스 바이트의 SHA256.
    Raises:
        OSError: 소스 파일을 읽을 수 없는 경우.
    Side Effects:
        저장소의 Python 소스만 읽는다.
    """
    repo = Path(__file__).resolve().parents[4]
    digest = hashlib.sha256()
    for base in (repo / "parser/src", repo / "parser_ai/src"):
        for path in sorted(base.rglob("*.py")):
            digest.update(str(path.relative_to(repo)).encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
    return digest.hexdigest()


def run_comparison(
    files: list[Path], manifest: dict, *, engines: tuple[str, ...] = ("rule", "ai"),
    repeats: int = 1, cache_mode: str = "cold", review: bool = True,
    allow_api: bool = False, client=None, truth: list[dict] | None = None,
    max_exception_calls: int = 0, progress=None,
) -> dict:
    """두 파서를 동일 원본·검증 조건에서 순차 실행하고 독립 평가한다.

    Args:
        files: 선택한 원본 파일 목록.
        manifest: 평가기만 사용하는 독립 합계 정답.
        engines: rule, ai 중 실행할 방법 목록.
        repeats: 반복 횟수.
        cache_mode: cold는 반복 시작 때 초기화, warm은 반복 간 캐시 유지.
        review: AI 독립 검토 여부.
        allow_api: 실제 AI 요청을 명시적으로 허용했는지 여부.
        client: 실행 전체 호출 예산을 공유하는 메모리 응답 캐시 클라이언트.
        truth: 평가기에만 전달하는 독립 셀 정답.
        max_exception_calls: 파일별 예외 요청 상한.
        progress: 진행 문자열을 받는 선택 함수.
    Returns:
        실행 조건과 파일별 결과, 반복별 집계를 가진 메모리 보고서.
    Raises:
        ValueError: 실험 조건 또는 정답 계약이 잘못된 경우.
    Side Effects:
        원본 읽기와 명시적으로 허용된 AI 통신만 수행한다. 파일을 쓰지 않는다.
    """
    if not engines or set(engines) - {"rule", "ai"} or len(set(engines)) != len(engines):
        raise ValueError("engines는 중복 없는 rule/ai 목록이어야 합니다")
    if repeats < 1 or cache_mode not in {"cold", "warm"}:
        raise ValueError("반복 횟수 또는 캐시 모드가 잘못됐습니다")
    if "ai" in engines and (not allow_api or client is None or not isinstance(getattr(client, "response_cache", None), dict)):
        raise ValueError("AI 비교에는 allow_api와 메모리 캐시 클라이언트가 필요합니다")
    entries = {e["filename"]: e for e in manifest["files"]}
    if len({p.name for p in files}) != len(files) or not files:
        raise ValueError("입력 파일명은 비어 있지 않고 유일해야 합니다")
    for path in files:
        score_manifest([], entries[path.name])
    if truth is not None:
        score_records([], truth)
    stores = {role: {"responses": {}, "templates": {}} for role in ("organization", "master")}
    trials = []
    source_hashes: dict[str, str | None] = {}
    started = time.perf_counter()
    for repetition in range(1, repeats + 1):
        if cache_mode == "cold":
            for store in stores.values():
                store["responses"].clear()
                store["templates"].clear()
        for file_index, path in enumerate(files):
            entry = entries[path.name]
            role = "master" if entry["is_master"] else "organization"
            order = engines if (repetition + file_index) % 2 else engines[::-1]
            for engine in order:
                if progress:
                    progress(f"[{repetition}/{repeats}] {engine}: {path.name}")
                if engine == "rule":
                    result = run_rule_adapter(path)
                else:
                    client.response_cache = stores[role]["responses"]
                    result = run_ai_adapter(path, client=client, allow_api=allow_api,
                                            template_cache=stores[role]["templates"], review=review,
                                            max_exception_calls=max_exception_calls)
                report = _summarize_result(result, entry, truth)
                first_hash = source_hashes.setdefault(str(path), result["source_integrity"].get("sha256_before"))
                consistent = bool(first_hash and first_hash == result["source_integrity"].get("sha256_before")
                                  and first_hash == result["source_integrity"].get("sha256_after"))
                report["source_consistent_across_trials"] = consistent
                if not consistent:
                    report["completed"] = False
                    report["errors"].append({"stage": "comparison_integrity", "error_type": "SourceVersionMismatch",
                                              "message": "동일 파일의 엔진·반복 간 원본 해시가 일치하지 않습니다"})
                report.update(repetition=repetition, input_role=role,
                              cache_at_repetition_start="empty" if repetition == 1 or cache_mode == "cold" else "retained")
                trials.append(report)
    return {"contract_version": "parser-comparison-v1", "mode": "run",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "conditions": {"engines": list(engines), "repeats": repeats,
                           "cache_mode": cache_mode, "cache_storage": "memory_only",
                           "cache_scope": "within_dataset; organizations_and_master_separate",
                           "review": review, "max_exception_calls": max_exception_calls,
                           "model": client.model if client else None,
                           "effort": client.effort if client else None,
                           "timeout_seconds": client.timeout if client else None,
                           "max_output_tokens": getattr(client, "max_output_tokens", None),
                           "max_calls_entire_experiment": client.max_calls if client else 0,
                           "python": platform.python_version(),
                           "platform": "-".join((platform.system(), platform.release(), platform.machine())),
                           "implementation_sha256": implementation_fingerprint(),
                           "engine_metadata": {}, "execution": "sequential_alternating_order",
                           "timing_includes": "source_hashes,extraction,raw_validation,interpretation,provenance",
                           "timing_excludes": "independent_scoring,report_serialization",
                           "manifest_version": manifest["version"],
                           "cell_truth_provided": truth is not None},
            "experiment_seconds": time.perf_counter() - started,
            "source_consistent_across_trials": all(r["source_consistent_across_trials"] for r in trials),
            "trials": trials, "summary": summarize_trials(trials),
            "limitations": ["합계 일치는 조직·항목·기간의 의미 정확도가 아님",
                            "현재 파서의 생략 규칙을 변경하지 않으며 미처리 금액도 정답 분모에 포함",
                            "첫 warm 반복은 빈 캐시로 시작하며 실제 적중은 inference에서 확인",
                            "실패 요청의 사용량·청구 비용은 응답이 없으면 확인 불가",
                            "현재 합계 정답 계약은 기본 더미 원본 양식 전용"]}
