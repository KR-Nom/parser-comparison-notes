"""비교 실행의 정답 격리·캐시 정책·출력 보호를 메모리에서 검증한다."""

import copy
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from excel_parser.benchmark.cli import main, save_report
from excel_parser.benchmark.runner import load_dataset, run_comparison


def manifest_fixture() -> dict:
    """합성 기본 더미 정답 계약을 만든다.

    Args:
        없음.
    Returns:
        조직 한 개와 마스터 한 개의 합계 정답.
    Raises:
        없음.
    Side Effects:
        메모리 객체만 생성한다.
    """
    expected = {}
    for kind in ("revenue", "cost"):
        expected[kind] = {"records": 1, "periods": {
            period: {"monthly": [1] * 12, "annual": 12}
            for period in ("prior", "plan", "outlook", "next_plan")}}
    expected["cost"]["next_projection"] = {"monthly": [1] * 12, "annual": 12}
    return {"version": "baseline_strict_native_input_only_v1", "files": [
        {"filename": "org.xlsx", "is_master": False, "org_code": "SECRET_GOLD_ORG", "expected": expected},
        {"filename": "master.xlsx", "is_master": True, "org_code": "ALL", "expected": copy.deepcopy(expected)},
    ]}


def adapter_fixture(engine: str) -> dict:
    """평가기에서 누락을 실제로 계산할 빈 예측 결과를 만든다.

    Args:
        engine: 비교 방법 이름.
    Returns:
        실제 어댑터와 같은 형태의 합성 보고서.
    Raises:
        없음.
    Side Effects:
        메모리 객체만 생성한다.
    """
    return {"parser": engine, "source": {"path": "/memory/org.xlsx"},
            "mapped": {"records": []}, "workbook": {}, "semantic": {},
            "validation": {"extraction": {"passed": True, "issues": []},
                           "provenance": {"passed": True, "issues": []}},
            "source_integrity": {"unchanged": True, "sha256_before": "stable", "sha256_after": "stable"}, "timings": {"total_seconds": 1.0},
            "inference": {"logical_attempted_calls": 0, "http_post_attempts": 0},
            "errors": [], "completed": True}


class BenchmarkRunnerTests(unittest.TestCase):
    """실제 API·파일 생성 없이 비교 실행의 경계를 검사한다."""

    def test_master_excluded_by_default_and_names_validated(self) -> None:
        """마스터 기본 제외와 manifest 경로 탈출 방지를 확인한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 선택 또는 경로 검증이 잘못된 경우.
        Side Effects:
            파일 읽기를 메모리 응답으로 대체한다.
        """
        data = manifest_fixture()
        with patch.object(Path, "read_text", return_value=json.dumps(data)), patch.object(Path, "is_file", return_value=True):
            files, _, _ = load_dataset(Path("/memory"))
            self.assertEqual([p.name for p in files], ["org.xlsx"])
            files, _, _ = load_dataset(Path("/memory"), include_master=True)
            self.assertEqual(len(files), 2)
        data["files"][0]["filename"] = "../escape.xlsx"
        with patch.object(Path, "read_text", return_value=json.dumps(data)):
            with self.assertRaises(ValueError):
                load_dataset(Path("/memory"))

    def test_truth_never_passes_to_engine_and_missing_is_scored(self) -> None:
        """정답을 엔진에 전달하지 않고 빈 결과도 분모에 포함한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 정답 유출 또는 누락 분모 제외가 발생한 경우.
        Side Effects:
            엔진 실행을 메모리 결과로 대체한다.
        """
        with patch("excel_parser.benchmark.runner.run_rule_adapter", return_value=adapter_fixture("rule")) as adapter:
            report = run_comparison([Path("/memory/org.xlsx")], manifest_fixture(), engines=("rule",))
        adapter.assert_called_once_with(Path("/memory/org.xlsx"))
        trial = report["trials"][0]
        self.assertEqual(trial["financial_aggregate_evaluation"]["groups_total"], 117)
        self.assertEqual(trial["financial_aggregate_evaluation"]["matching_groups"], 0)
        self.assertIsNone(trial["cell_evaluation"]["accuracy"])
        self.assertNotIn("workbook", trial)
        self.assertNotIn("mapped", trial)

    def test_cache_resets_by_repetition_and_master_is_separate(self) -> None:
        """캐시 정책과 마스터 저장소 격리를 확인한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 반복 또는 입력 역할 간 캐시가 잘못 섞인 경우.
        Side Effects:
            대체 AI 함수가 메모리 캐시만 변경한다.
        """
        for mode, expected in (("cold", [0, 0, 0, 0]), ("warm", [0, 0, 1, 1])):
            observations = []
            client = SimpleNamespace(response_cache={}, model="fake", effort="low", timeout=1, max_calls=10)

            def fake_ai(path, **kwargs):
                """호출 전 캐시 크기를 기록하고 합성 캐시 한 건을 넣는다.

                Args:
                    path: 사용하지 않는 합성 입력 경로.
                    kwargs: 실제 어댑터와 같은 호출 옵션.
                Returns:
                    합성 AI 어댑터 결과.
                Raises:
                    없음.
                Side Effects:
                    메모리 관측 목록과 캐시 사전을 갱신한다.
                """
                observations.append(len(kwargs["client"].response_cache))
                kwargs["client"].response_cache["key"] = "cached"
                kwargs["template_cache"]["key"] = "template"
                return adapter_fixture("ai")

            with patch("excel_parser.benchmark.runner.run_ai_adapter", side_effect=fake_ai):
                report = run_comparison([Path("/memory/org.xlsx"), Path("/memory/master.xlsx")],
                                        manifest_fixture(), engines=("ai",), repeats=2, cache_mode=mode,
                                        client=client, allow_api=True)
            self.assertEqual(observations, expected)
            self.assertEqual(len(report["summary"]), 4)

    def test_api_gate_runs_before_loading_data(self) -> None:
        """명시적 API 실행 선택이 없으면 입력 처리 전 종료한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 미허용 API 경로가 입력을 읽은 경우.
        Side Effects:
            콘솔 출력을 메모리에 받는다.
        """
        with patch("excel_parser.benchmark.cli.load_dataset") as load, redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            self.assertEqual(main(["run", "/memory"]), 2)
        load.assert_not_called()

    def test_output_outside_allowed_root_is_rejected_without_writes(self) -> None:
        """Data와 임의 외부 경로의 출력 요청을 파일 생성 전에 거부한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 출력 보호 전에 디스크 쓰기를 시도한 경우.
        Side Effects:
            쓰기 함수를 차단하며 파일을 만들지 않는다.
        """
        with patch.object(Path, "mkdir", side_effect=AssertionError("쓰기 금지")):
            for output in (Path("/tmp/result"), Path("/repo/Data/result"), Path("/repo/parser/output/comparisons")):
                with self.assertRaises(ValueError):
                    save_report({}, output, Path("/repo"))

    def test_invalid_manifest_rejected_before_engine_calls(self) -> None:
        """정답 수치가 무효면 비용 발생 전에 실행을 거부한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 잘못된 정답 상태에서 엔진을 실행한 경우.
        Side Effects:
            엔진 호출을 감시하며 실제 실행하지 않는다.
        """
        manifest = manifest_fixture()
        manifest["files"][0]["expected"]["revenue"]["periods"]["prior"]["annual"] = "NaN"
        with patch("excel_parser.benchmark.runner.run_rule_adapter") as adapter:
            with self.assertRaises(ValueError):
                run_comparison([Path("/memory/org.xlsx")], manifest, engines=("rule",))
        adapter.assert_not_called()

    def test_cross_trial_source_change_invalidates_comparison(self) -> None:
        """각 실행 중에는 유지됐어도 실행 사이에 바뀐 원본은 탐지한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 서로 다른 원본 버전을 유효한 비교로 처리한 경우.
        Side Effects:
            엔진 실행을 메모리 결과로 대체한다.
        """
        first, second = adapter_fixture("rule"), adapter_fixture("rule")
        second["source_integrity"].update(sha256_before="changed", sha256_after="changed")
        with patch("excel_parser.benchmark.runner.run_rule_adapter", side_effect=[first, second]):
            report = run_comparison([Path("/memory/org.xlsx")], manifest_fixture(), engines=("rule",), repeats=2)
        self.assertFalse(report["source_consistent_across_trials"])
        self.assertFalse(report["trials"][1]["completed"])

    def test_bad_output_and_nonfinite_timeout_are_preflight_errors(self) -> None:
        """잘못된 출력·시간 설정을 원본과 API에 접근하기 전에 거부한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 구성 오류 이후 입력 처리가 시작된 경우.
        Side Effects:
            콘솔을 메모리에 받고 입력 접근을 감시한다.
        """
        cases = [["--timeout", "nan"], ["--timeout", "inf"],
                 ["--write-output", "--output-dir", "/tmp/not-allowed"]]
        for flags in cases:
            with patch("excel_parser.benchmark.cli.load_dataset") as load, redirect_stderr(io.StringIO()):
                self.assertEqual(main(["run", "/memory", "--engines", "rule", *flags]), 2)
            load.assert_not_called()
