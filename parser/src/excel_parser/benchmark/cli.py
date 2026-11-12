"""기본은 콘솔만 사용하며 실제 AI 통신과 결과 저장을 명시적으로 선택한다."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from excel_parser.extraction import file_fingerprint

from .runner import load_dataset, load_truth, plan_comparison, run_comparison


def json_value(value: object) -> str:
    """정밀 Decimal을 JSON 문자열로 변환한다.

    Args:
        value: 기본 JSON 인코더가 처리하지 못한 값.
    Returns:
        유한 Decimal의 고정 소수점 문자열.
    Raises:
        TypeError: 지원하지 않는 값 또는 비유한 숫자인 경우.
    Side Effects:
        없음.
    """
    if isinstance(value, Decimal) and value.is_finite():
        return format(value, "f")
    raise TypeError("JSON 변환을 지원하지 않는 값입니다: " + type(value).__name__)


def validate_output_path(output: Path, repo: Path) -> Path:
    """비용이 발생하기 전에 출력 경로의 안전성과 덮어쓰기를 검사한다.

    Args:
        output: 요청한 새 실행 폴더.
        repo: 저장소 루트.
    Returns:
        검증된 절대 경로.
    Raises:
        ValueError: 출력 허용 범위를 벗어난 경우.
        FileExistsError: 경로가 이미 존재하는 경우.
    Side Effects:
        경로와 존재 여부만 읽는다. 폴더를 생성하지 않는다.
    """
    output = output.expanduser().resolve()
    allowed = (repo / "parser/output/comparisons").resolve()
    if output == allowed or not output.is_relative_to(allowed) or any(p.lower() == "data" for p in output.parts):
        raise ValueError("출력은 parser/output/comparisons 아래 새 폴더만 허용합니다")
    if output.exists():
        raise FileExistsError("기존 결과를 덮어쓰지 않습니다: " + str(output))
    return output


def save_report(report: dict, output: Path, repo: Path) -> Path:
    """허용 출력 경로의 새 폴더에 JSON과 요약 CSV를 저장한다.

    Args:
        report: 원본 객체가 제외된 비교 보고서.
        output: 새 실행 폴더 경로.
        repo: 현재 저장소 루트.
    Returns:
        생성한 실행 폴더 절대 경로.
    Raises:
        ValueError: 허용 출력 디렉터리 밖이거나 Data 내부인 경우.
        FileExistsError: 기존 경로를 덮어쓰려는 경우.
        OSError: 저장 실패.
    Side Effects:
        명시적으로 요청된 새 폴더와 comparison.json, summary.csv를 생성한다.
    """
    output = validate_output_path(output, repo)
    encoded = json.dumps(report, ensure_ascii=False, indent=2, default=json_value, allow_nan=False)
    output.mkdir(parents=True, exist_ok=False)
    (output / "comparison.json").write_text(encoded + "\n", encoding="utf-8")
    rows = report.get("summary", [])
    with (output / "summary.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        columns = list(rows[0]) if rows else ["parser", "repetition", "input_role"]
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return output


def make_parser() -> argparse.ArgumentParser:
    """비교 계획과 실제 실행의 명령행 옵션을 정의한다.

    Args:
        없음.
    Returns:
        부작용 없이 생성한 명령행 파서.
    Raises:
        없음.
    Side Effects:
        없음.
    """
    parser = argparse.ArgumentParser(description="규칙 기반 vs LLM 구조 파서 비교: 기본은 파일 저장 없음")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "run"):
        command = sub.add_parser(name)
        command.add_argument("dataset", type=Path)
        command.add_argument("--manifest", type=Path)
        command.add_argument("--include-master", action="store_true", help="마스터를 독립 입력·별도 집계로 포함")
        command.add_argument("--max-files", type=int)
        command.add_argument("--review", choices=("on", "off"), default="on")
        if name == "run":
            command.add_argument("--engines", choices=("both", "rule", "ai"), default="both")
            command.add_argument("--allow-api", action="store_true", help="AI 데이터 전송과 유료 API 요청을 명시적으로 실행")
            command.add_argument("--truth", type=Path, help="독립 셀 정답 JSON. 없으면 의미 정확도는 미측정")
            command.add_argument("--repeats", type=int, default=1)
            command.add_argument("--cache", choices=("cold", "warm"), default="cold")
            command.add_argument("--model")
            command.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max"), default="high")
            command.add_argument("--timeout", type=float, default=240)
            command.add_argument("--max-output-tokens", type=int, default=32768)
            command.add_argument("--max-calls", type=int, default=300, help="전체 실험의 캐시 제외 논리 호출 상한; 금액 상한 아님")
            command.add_argument("--max-exception-calls", type=int, default=0)
            command.add_argument("--write-output", action="store_true")
            command.add_argument("--output-dir", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    """계획 또는 비교를 실행하고 결과를 콘솔에 출력한다.

    Args:
        argv: 선택적 명령행 인자 목록.
    Returns:
        처리 완료 0, 입력·구성·검증·처리 실패 2, 사용자 중단 130.
    Raises:
        SystemExit: argparse의 도움말 또는 잘못된 명령행 처리.
    Side Effects:
        원본·정답 읽기와 콘솔 출력. allow-api 때만 통신하고 write-output 때만 저장한다.
    """
    parser = make_parser()
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[4]
    output = None
    try:
        if args.command == "run":
            if args.engines != "rule" and not args.allow_api:
                raise ValueError("AI 실행에는 --allow-api가 필요합니다. 요청량만 보려면 plan을 사용하세요")
            if args.output_dir and not args.write_output:
                raise ValueError("--output-dir에는 --write-output이 필요합니다")
            if args.repeats < 1 or args.max_calls < 1 or not math.isfinite(args.timeout) or args.timeout <= 0 or args.max_output_tokens < 1 or args.max_exception_calls < 0:
                raise ValueError("반복·호출·시간·토큰은 양수, 예외 호출은 0 이상이어야 합니다")
            if args.write_output:
                name = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
                output = validate_output_path(args.output_dir or repo / "parser/output/comparisons" / name, repo)
        files, manifest, manifest_path = load_dataset(args.dataset, args.manifest,
                                                     include_master=args.include_master, max_files=args.max_files)
        if args.command == "plan":
            report = plan_comparison(files, review=args.review == "on")
        else:
            truth = load_truth(args.truth, files)
            evaluation_sources = {"manifest": str(manifest_path),
                                  "manifest_sha256": file_fingerprint(manifest_path),
                                  "truth": str(args.truth.resolve()) if args.truth else None,
                                  "truth_sha256": file_fingerprint(args.truth) if args.truth else None,
                                  "hash_capture": "before_engine_execution",
                                  "provided_to_engines": False}
            client = None
            if args.engines != "rule":
                from excel_parser_ai.client import ResponsesClient, read_env

                read_env(repo / "parser_ai/.env")
                if not os.environ.get("OPENAI_API_KEY"):
                    raise ValueError("OPENAI_API_KEY가 없습니다. plan 또는 --engines rule은 API 없이 실행됩니다")
                client = ResponsesClient(repo / "parser_ai/cache", response_cache={},
                                         model=args.model, effort=args.effort, timeout=args.timeout,
                                         max_output_tokens=args.max_output_tokens, max_calls=args.max_calls,
                                         progress=lambda message: print(message, file=sys.stderr, flush=True))
            engines = ("rule", "ai") if args.engines == "both" else (args.engines,)
            report = run_comparison(files, manifest, engines=engines, repeats=args.repeats,
                                    cache_mode=args.cache, review=args.review == "on", allow_api=args.allow_api,
                                    client=client, truth=truth, max_exception_calls=args.max_exception_calls,
                                    progress=lambda message: print(message, file=sys.stderr, flush=True))
            report["evaluation_sources"] = evaluation_sources
            if args.write_output:
                saved = save_report(report, output, repo)
                print("비교 결과 저장: " + str(saved), file=sys.stderr)
        print(json.dumps(report, ensure_ascii=False, indent=2, default=json_value, allow_nan=False))
        if args.command == "run":
            valid = all(r["completed"] and r["source_integrity"]["unchanged"]
                        and r["validation"]["extraction"]["passed"] is True
                        and r["validation"]["provenance"]["passed"] is True for r in report["trials"])
            return 0 if valid else 2
        return 0
    except (ValueError, OSError, KeyError, TypeError, ImportError) as error:
        print("비교 실행 오류: " + str(error), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("비교 중단: 메모리 실행이며 완료 보고서는 생성되지 않았습니다", file=sys.stderr)
        return 130
