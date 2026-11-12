"""파일과 API 없이 독립 셀 정답 평가의 분모·출처·금액 계약을 검증한다."""

from copy import deepcopy
from decimal import Decimal
import unittest

from excel_parser.benchmark.evaluation import score_records


def truth_row(cell: str = "H5", **changes: object) -> dict:
    """테스트가 직접 선언한 독립 정답 행을 생성한다.

    Args:
        cell: 정답 셀 주소.
        changes: 개별 검증을 위해 바꿀 정답 필드.
    Returns:
        필수 출처·의미·금액을 가진 새 사전.
    Raises:
        없음.
    Side Effects:
        없음. 메모리만 사용한다.
    """
    return {
        "source_file": "organization.xlsx", "sheet": "매출", "cell": cell,
        "organization_code": "F10", "year": 2026, "month": 1,
        "aggregation": "month", "scenario": "actual", "normalized_value": "1200",
        **changes,
    }


def standard_record(cell: str = "H5") -> dict:
    """정답 생성과 분리해 공통 출력 계약의 예측 레코드를 직접 선언한다.

    Args:
        cell: 원본 출처 셀 주소.
    Returns:
        중첩된 표준 레코드 사전.
    Raises:
        없음.
    Side Effects:
        없음. 파일이나 정답 생성기를 조회하지 않는다.
    """
    return {
        "source": {"path": "/inputs/organization.xlsx", "sheet": "매출", "cell": cell},
        "organization": {"code": "F10"},
        "item": {"code": "REV001", "name": "매출액"},
        "period": {"year": 2026, "month": 1, "aggregation": "month", "scenario": "actual"},
        "value": {"normalized_value": Decimal("1200.00"), "source_unit": "천원", "status": "confirmed"},
        "mapping": {"status": "mapped"},
    }


class RecordEvaluationTests(unittest.TestCase):
    """공통 분모를 유지하면서 누락·중복·불확실성을 분리하는 평가를 확인한다."""

    def test_exact_decimal_value_and_input_objects_are_preserved(self) -> None:
        """숫자 표현 차이는 허용하고 입력 객체는 변경하지 않는지 확인한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 정확한 십진수 비교 또는 입력 보존이 실패한 경우.
        Side Effects:
            메모리 객체만 생성한다.
        """
        records, truth = [standard_record()], [truth_row()]
        before = deepcopy((records, truth))
        report = score_records(records, truth)
        self.assertEqual(report["exact_match"], 1)
        self.assertEqual(report["accuracy"], 1.0)
        self.assertEqual(report["semantic_exact"], 1)
        self.assertEqual(report["value_exact"], 1)
        self.assertEqual((records, truth), before)

    def test_missing_prediction_stays_in_denominator(self) -> None:
        """해석하지 못한 정답 셀이 정확도 분모에서 사라지지 않게 한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 누락을 제외해 정확도가 높아진 경우.
        Side Effects:
            메모리만 사용한다.
        """
        report = score_records([standard_record()], [truth_row(), truth_row("I5")])
        self.assertEqual(report["total_expected"], 2)
        self.assertEqual(report["missing"], 1)
        self.assertEqual(report["accuracy"], 0.5)

    def test_duplicate_predictions_cannot_select_the_correct_candidate(self) -> None:
        """중복 출력 중 정답 하나만 골라 점수를 얻지 못하도록 검증한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 중복 출처에 정답 점수가 주어진 경우.
        Side Effects:
            예측 사전만 메모리에서 수정한다.
        """
        wrong = standard_record()
        wrong["value"]["normalized_value"] = "999"
        report = score_records([standard_record(), wrong], [truth_row()])
        self.assertEqual(report["duplicate_sources"], 1)
        self.assertEqual(report["duplicate_records"], 1)
        self.assertEqual(report["exact_match"], 0)
        self.assertEqual(report["value_exact"], 0)
        self.assertEqual(report["details"][0]["source_status"], "duplicate")

    def test_extra_and_invalid_sources_are_reported_separately(self) -> None:
        """범위 밖 출력과 출처 없는 출력이 별도 계수되는지 확인한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 추가·무효 출력이 정답이나 누락으로 잘못 집계된 경우.
        Side Effects:
            메모리만 사용한다.
        """
        report = score_records(
            [standard_record(), standard_record("Z9"), standard_record("Z9"), {}], [truth_row()]
        )
        self.assertEqual(report["extra_sources"], 1)
        self.assertEqual(report["extra_records"], 2)
        self.assertEqual(report["invalid_records"], 1)
        self.assertEqual(report["duplicate_sources"], 1)
        self.assertEqual(report["missing"], 0)
        self.assertEqual(report["accuracy"], 1.0)

    def test_wrong_file_does_not_match_the_same_sheet_and_cell(self) -> None:
        """다른 파일의 동일 좌표를 정답으로 인정하지 않는지 확인한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 파일 식별이 무시된 경우.
        Side Effects:
            메모리의 예측 출처만 바꾼다.
        """
        record = standard_record()
        record["source"]["path"] = "/inputs/another.xlsx"
        report = score_records([record], [truth_row()])
        self.assertEqual(report["missing"], 1)
        self.assertEqual(report["extra_sources"], 1)
        self.assertEqual(report["accuracy"], 0.0)

    def test_partial_mapping_can_have_exact_value_without_full_credit(self) -> None:
        """의미 미확정 상태의 올바른 금액을 전체 정답과 분리한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: partial 결과가 전체 정답이 되거나 금액 정답이 사라진 경우.
        Side Effects:
            메모리의 상태 필드만 바꾼다.
        """
        record = standard_record()
        record["mapping"]["status"] = "partial"
        report = score_records([record], [truth_row()])
        self.assertEqual(report["unresolved"], 1)
        self.assertEqual(report["semantic_exact"], 0)
        self.assertEqual(report["value_exact"], 1)
        self.assertEqual(report["exact_match"], 0)

    def test_semantic_mismatch_is_distinct_from_amount_mismatch(self) -> None:
        """같은 금액을 잘못된 조직·기간에 배치한 오류를 분리한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 의미와 금액 일치가 혼동된 경우.
        Side Effects:
            메모리 객체만 변경한다.
        """
        record = standard_record()
        record["organization"]["code"] = "F12"
        record["period"]["month"] = 2
        report = score_records([record], [truth_row()])
        self.assertEqual(report["semantic_mismatch"], 1)
        self.assertEqual(report["value_exact"], 1)
        self.assertEqual(report["exact_match"], 0)
        self.assertEqual(report["details"][0]["mismatched_fields"], ["organization_code", "month"])

    def test_optional_fields_are_compared_only_when_present_in_truth(self) -> None:
        """선택 정답 필드가 없으면 추측하지 않고 있을 때는 엄격히 비교한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 선택 필드의 평가 범위가 틀린 경우.
        Side Effects:
            메모리만 사용한다.
        """
        record = standard_record()
        self.assertEqual(score_records([record], [truth_row()])["exact_match"], 1)
        report = score_records([record], [truth_row(item_code=None, item_name="다른 항목", source_unit="원")])
        self.assertEqual(report["details"][0]["mismatched_fields"], ["item_code", "item_name", "source_unit"])
        self.assertEqual(report["value_exact"], 1)
        self.assertEqual(report["exact_match"], 0)

    def test_unconfirmed_or_nonfinite_values_are_unresolved(self) -> None:
        """미확정 상태와 비유한 수치를 금액 정답으로 인정하지 않는다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 잘못된 숫자나 상태가 정답 점수를 얻은 경우.
        Side Effects:
            메모리에서 각 숫자 사례를 만든다.
        """
        cases = [("1200", "unresolved"), ("NaN", "confirmed"), ("Infinity", "confirmed"), (True, "confirmed")]
        for amount, status in cases:
            with self.subTest(amount=amount, status=status):
                record = standard_record()
                record["value"].update(normalized_value=amount, status=status)
                report = score_records([record], [truth_row()])
                self.assertEqual(report["unresolved"], 1)
                self.assertEqual(report["semantic_exact"], 1)
                self.assertEqual(report["value_exact"], 0)
                self.assertEqual(report["exact_match"], 0)

    def test_numeric_mismatch_is_not_reported_as_unresolved(self) -> None:
        """확정했지만 틀린 금액은 미해결 상태와 구분한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 틀린 금액의 상태가 잘못 집계된 경우.
        Side Effects:
            메모리의 숫자만 바꾼다.
        """
        record = standard_record()
        record["value"]["normalized_value"] = "1200.00000000000000000001"
        report = score_records([record], [truth_row()])
        self.assertEqual(report["value_mismatch"], 1)
        self.assertEqual(report["unresolved"], 0)
        self.assertEqual(report["accuracy"], 0.0)

    def test_missing_status_and_flat_prediction_do_not_gain_credit(self) -> None:
        """출력 계약이 없는 예측을 암묵적으로 확정 상태로 보완하지 않는다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 계약 누락을 자동 보완해 점수를 준 경우.
        Side Effects:
            메모리에서 상태 키를 제거한다.
        """
        record = standard_record()
        del record["mapping"]
        del record["value"]["status"]
        report = score_records([record], [truth_row()])
        self.assertEqual(report["unresolved"], 1)
        self.assertEqual(report["exact_match"], 0)
        flat_report = score_records([truth_row()], [truth_row()])
        self.assertEqual(flat_report["invalid_records"], 1)
        self.assertEqual(flat_report["missing"], 1)

    def test_invalid_or_duplicate_truth_is_rejected(self) -> None:
        """평가 분모와 정답 숫자 자체가 무효일 때 조용히 진행하지 않는다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 무효 정답이 허용된 경우.
        Side Effects:
            메모리에서 정답 경계 사례를 만든다.
        """
        missing = truth_row()
        del missing["scenario"]
        cases = [[truth_row(), truth_row()], [missing], [truth_row(normalized_value=None)],
                 [truth_row(normalized_value="NaN")], [truth_row(cell="H0")], [None]]
        for rows in cases:
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    score_records([], rows)

    def test_empty_truth_has_no_accuracy_and_does_not_hide_extra_outputs(self) -> None:
        """정답이 없는 경우 100퍼센트 성공으로 표시하지 않는다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 빈 정답을 성공 점수로 표시하거나 추가 출력을 숨긴 경우.
        Side Effects:
            메모리만 사용한다.
        """
        report = score_records([standard_record()], [])
        self.assertIsNone(report["accuracy"])
        self.assertEqual(report["total_expected"], 0)
        self.assertEqual(report["extra_sources"], 1)

    def test_detail_limit_keeps_full_counts(self) -> None:
        """상세 목록 상한이 전체 누락 계수를 잘라내지 않는지 확인한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 상세 상한이나 전체 계수가 틀린 경우.
        Side Effects:
            메모리 정답 103개만 생성한다.
        """
        report = score_records([], (truth_row(f"H{row}") for row in range(1, 104)))
        self.assertEqual(report["missing"], 103)
        self.assertEqual(len(report["details"]), 100)
        self.assertEqual(report["details_truncated"], 3)
        self.assertEqual(report["accuracy"], 0.0)


if __name__ == "__main__":
    unittest.main()
