"""합성 OOXML과 대체 전송으로 실제 벤치마크 경로의 독립성과 무저장을 검증한다."""

import hashlib
import json
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from helpers import PROJECT_ROOT, extract_memory, make_package, open_memory_archive

from excel_parser.benchmark.adapters import run_ai_adapter, run_rule_adapter
from excel_parser.validation import validate_extraction
from excel_parser_ai.client import ModelError, ResponsesClient


def _reference_response(body: dict) -> dict:
    """합성 표의 후보를 근거가 있는 참조 영역으로 선언하는 응답을 만든다.

    Args:
        body: 실제 ResponsesClient가 구성한 API 요청 사전.
    Returns:
        구조 엔진의 실제 검증을 통과하는 Responses 형식의 응답.
    Raises:
        KeyError: 요청의 필수 모델 필드가 없는 경우.
    Side Effects:
        없음. 네트워크와 파일에 접근하지 않는다.
    """
    plan = {
        "sheet": "검증표", "kind": "reference", "tables": [],
        "excluded_areas": [{
            "start_row": 2, "end_row": 3, "start_col": "B", "end_col": "B",
            "reason": "합성 참조 영역", "evidence": [{"sheet": "검증표", "cell": "A1"}],
        }],
        "uncertainties": [],
    }
    return {
        "status": "completed", "model": body["model"],
        "output": [{"content": [{"type": "output_text", "text": json.dumps({"plan": plan})}]}],
        "usage": {"input_tokens": 12, "output_tokens": 8},
    }


@contextmanager
def _memory_io(package: bytes, *, fingerprints: list[str] | None = None):
    """원본 읽기 경계만 메모리로 대체하고 모든 결과 쓰기를 금지한다.

    Args:
        package: 합성 OOXML ZIP 바이트.
        fingerprints: 원본 변경 검사를 위한 선택 해시 반환 순서.
    Returns:
        새로 수행한 실제 추출의 호출을 확인할 mock을 yield한다.
    Raises:
        AssertionError: 어댑터 또는 파서가 디스크 캐시나 파일 쓰기를 시도한 경우.
    Side Effects:
        테스트 범위의 파일 경계만 일시 대체한다. 파일을 만들지 않는다.
    """
    digest = hashlib.sha256(package).hexdigest()
    with (
        patch("excel_parser.benchmark.adapters.file_fingerprint", return_value=digest, side_effect=fingerprints),
        patch(
            "excel_parser.benchmark.adapters.extract_workbook",
            side_effect=lambda path, **kwargs: extract_memory(package, name=path.name),
        ) as extraction,
        patch("excel_parser.validation.ZipFile", side_effect=lambda _: open_memory_archive(package)),
        patch.object(Path, "mkdir", side_effect=AssertionError("폴더 생성 금지")),
        patch.object(Path, "write_text", side_effect=AssertionError("텍스트 쓰기 금지")),
        patch.object(Path, "write_bytes", side_effect=AssertionError("바이트 쓰기 금지")),
        patch.object(Path, "read_text", side_effect=AssertionError("디스크 캐시 읽기 금지")),
        patch.object(Path, "replace", side_effect=AssertionError("파일 교체 금지")),
        patch("urllib.request.urlopen", side_effect=AssertionError("테스트 중 실제 API 금지")),
    ):
        yield extraction


class BenchmarkAdapterTests(unittest.TestCase):
    """실제 파서와 검증기를 우회하지 않고 실행 경계 계약을 확인한다."""

    def test_rule_executes_real_extraction_mapping_and_validation_without_output(self) -> None:
        """규칙 실행이 원본 검증부터 출처 검증까지 저장 없이 수행되는지 확인한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 실제 단계가 누락되거나 파일 쓰기가 발생한 경우.
        Side Effects:
            합성 메모리 자료만 사용한다.
        """
        with _memory_io(make_package()) as extraction:
            result = run_rule_adapter(PROJECT_ROOT / "__memory__/adapter.xlsx")
        self.assertEqual(extraction.call_count, 1)
        self.assertTrue(result["completed"], result["errors"])
        self.assertTrue(result["validation_passed"])
        self.assertEqual(len(result["mapped"]["records"]), 2)
        self.assertTrue(result["validation"]["extraction"]["passed"])
        self.assertTrue(result["validation"]["provenance"]["passed"])
        self.assertTrue(result["source_integrity"]["unchanged"])
        self.assertEqual(result["inference"]["http_post_attempts"], 0)
        for stage in ("extraction", "extraction_validation", "interpretation", "provenance_validation"):
            self.assertGreaterEqual(result["timings"][stage + "_seconds"], 0)
        self.assertGreaterEqual(result["timings"]["total_seconds"], sum(
            seconds for name, seconds in result["timings"].items() if name != "total_seconds"
        ))

    def test_ai_gate_and_disk_cache_rejection_happen_before_source_reads(self) -> None:
        """AI 허용과 메모리 캐시 설정을 원본 접근 이전에 강제한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 미허용 실행이 파일이나 모델에 접근한 경우.
        Side Effects:
            클라이언트 객체만 메모리에 만든다.
        """
        client = ResponsesClient("/unused/cache", transport=_reference_response)
        with patch("excel_parser.benchmark.adapters.file_fingerprint") as fingerprint:
            with self.assertRaisesRegex(ValueError, "allow_api"):
                run_ai_adapter(Path("synthetic.xlsx"), client=client)
            with self.assertRaisesRegex(ValueError, "response_cache"):
                run_ai_adapter(Path("synthetic.xlsx"), client=client, allow_api=True)
        fingerprint.assert_not_called()

    def test_each_parser_extracts_independently_and_ai_uses_real_structure_flow(self) -> None:
        """두 파서가 추출 객체를 공유하지 않고 AI 실제 구조 경로를 실행하는지 확인한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 추출 재사용, 디스크 저장 또는 실제 API 호출이 발생한 경우.
        Side Effects:
            합성 원본과 메모리 캐시만 사용한다.
        """
        transport = Mock(side_effect=_reference_response)
        client = ResponsesClient("/unused/cache", response_cache={}, transport=transport)
        with _memory_io(make_package()) as extraction:
            path = PROJECT_ROOT / "__memory__/adapter.xlsx"
            rule = run_rule_adapter(path)
            ai = run_ai_adapter(path, client=client, allow_api=True, review=False)
        self.assertEqual(extraction.call_count, 2)
        self.assertIsNot(rule["workbook"], ai["workbook"])
        self.assertIsNot(rule["workbook"]["sheets"][0]["cells"], ai["workbook"]["sheets"][0]["cells"])
        self.assertTrue(ai["completed"], ai["errors"])
        self.assertEqual(len(ai["semantic"]["structure_plans"]), 1)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(ai["inference"]["logical_attempted_calls"], 1)
        self.assertEqual(ai["inference"]["http_post_attempts"], 0)
        self.assertEqual(ai["inference"]["input_tokens"], 12)
        self.assertEqual(len(client.response_cache), 1)

    def test_shared_template_cache_and_counter_deltas_do_not_repeat_prior_usage(self) -> None:
        """후속 파일이 메모리 템플릿을 재사용해도 이전 호출량을 다시 합산하지 않는다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 캐시를 저장하거나 파일별 호출량을 중복 계산한 경우.
        Side Effects:
            공유 응답·구조 템플릿 사전만 갱신한다.
        """
        transport = Mock(side_effect=_reference_response)
        client = ResponsesClient("/unused/cache", response_cache={}, transport=transport)
        templates = {}
        with _memory_io(make_package()):
            path = PROJECT_ROOT / "__memory__/adapter.xlsx"
            first = run_ai_adapter(path, client=client, allow_api=True, template_cache=templates, review=False)
            second = run_ai_adapter(path, client=client, allow_api=True, template_cache=templates, review=False)
        self.assertTrue(first["completed"], first["errors"])
        self.assertTrue(second["completed"], second["errors"])
        self.assertEqual(first["inference"]["logical_attempted_calls"], 1)
        self.assertEqual(second["inference"]["logical_attempted_calls"], 0)
        self.assertEqual(second["inference"]["input_tokens"], 0)
        self.assertEqual(second["inference"]["events"], [])
        self.assertEqual(second["semantic"]["reused_sheets"], ["검증표"])
        self.assertEqual(transport.call_count, 1)
        self.assertTrue(templates)

    def test_partial_ai_failure_is_preserved_with_failed_completion(self) -> None:
        """구조 엔진이 보존한 미해결 후보를 완전 성공으로 표시하지 않는다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: AI 실패가 숨겨지거나 원본 후보가 유실된 경우.
        Side Effects:
            대체 전송 실패를 메모리 이벤트에 기록한다.
        """
        client = ResponsesClient(
            "/unused/cache", response_cache={},
            transport=Mock(side_effect=ModelError("합성 모델 실패")),
        )
        with _memory_io(make_package()):
            result = run_ai_adapter(
                PROJECT_ROOT / "__memory__/adapter.xlsx", client=client,
                allow_api=True, review=False,
            )
        self.assertFalse(result["completed"])
        self.assertEqual(result["errors"][0]["stage"], "interpretation")
        self.assertEqual(len(result["mapped"]["records"]), 2)
        self.assertTrue(result["validation"]["provenance"]["passed"])
        self.assertEqual(result["inference"]["failed_calls"], 1)
        self.assertEqual(result["inference"]["usage_missing_calls"], 1)
        self.assertIsNone(result["inference"]["cost_usd"])
        self.assertEqual(result["inference"]["cost_status"], "not_estimated")

    def test_extraction_validation_failure_does_not_skip_either_interpreter(self) -> None:
        """원본 검증 불일치를 보존하면서 두 파서 해석과 출처 검증을 끝까지 비교한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 검증 불일치로 해석을 생략하거나 검증 성공으로 표시한 경우.
        Side Effects:
            실제 원본 검증 결과에 합성 불일치만 메모리로 추가한다.
        """
        transport = Mock(side_effect=_reference_response)
        client = ResponsesClient("/unused/cache", response_cache={}, transport=transport)
        with (
            _memory_io(make_package()),
            patch(
                "excel_parser.benchmark.adapters.validate_extraction",
                side_effect=lambda path, workbook: {
                    **validate_extraction(path, workbook), "passed": False,
                    "issues": [{"code": "COMMENT_MISMATCH", "message": "합성 불일치"}],
                },
            ),
        ):
            path = PROJECT_ROOT / "__memory__/adapter.xlsx"
            rule = run_rule_adapter(path)
            ai = run_ai_adapter(path, client=client, allow_api=True, review=False)
        for result in (rule, ai):
            self.assertTrue(result["completed"], result["errors"])
            self.assertFalse(result["validation_passed"])
            self.assertFalse(result["validation"]["extraction"]["passed"])
            self.assertEqual(result["validation"]["extraction"]["issues"][0]["code"], "COMMENT_MISMATCH")
            self.assertTrue(result["validation"]["provenance"]["passed"])
            self.assertEqual(len(result["mapped"]["records"]), 2)
        self.assertEqual(transport.call_count, 1)

    def test_unlogged_response_parse_failure_has_unknown_usage_not_zero_cost(self) -> None:
        """응답 검증 중 이벤트 없이 실패한 논리 호출도 누락 사용량으로 남긴다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 누락 호출을 성공 또는 비용 0으로 해석한 경우.
        Side Effects:
            대체 전송만 사용하며 파일과 실제 API에 접근하지 않는다.
        """
        client = ResponsesClient(
            "/unused/cache", response_cache={},
            transport=Mock(return_value={"status": "incomplete", "output": []}),
        )
        with _memory_io(make_package()):
            result = run_ai_adapter(
                PROJECT_ROOT / "__memory__/adapter.xlsx", client=client,
                allow_api=True, review=False,
            )
        self.assertFalse(result["completed"])
        inference = result["inference"]
        self.assertEqual(inference["events"], [])
        self.assertEqual(inference["logical_attempted_calls"], 1)
        self.assertEqual(inference["api_calls"], 1)
        self.assertEqual(inference["unlogged_attempted_calls"], 1)
        self.assertEqual(inference["failed_calls"], 1)
        self.assertEqual(inference["usage_missing_calls"], 1)
        self.assertIsNone(inference["cost_usd"])
        self.assertEqual(inference["cost_status"], "not_estimated")

    def test_changed_source_hash_prevents_success(self) -> None:
        """처리 전후 원본 해시가 다르면 검증이 통과해도 성공으로 표시하지 않는다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 원본 변경을 성공으로 표시한 경우.
        Side Effects:
            해시 반환값만 대체하며 실제 원본은 변경하지 않는다.
        """
        with _memory_io(make_package(), fingerprints=["before", "after"]):
            result = run_rule_adapter(PROJECT_ROOT / "__memory__/adapter.xlsx")
        self.assertFalse(result["completed"])
        self.assertFalse(result["validation_passed"])
        self.assertFalse(result["source_integrity"]["unchanged"])
        self.assertEqual(result["errors"][0]["error_type"], "SourceChanged")


if __name__ == "__main__":
    unittest.main()
