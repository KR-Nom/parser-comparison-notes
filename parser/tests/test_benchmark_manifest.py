"""기본 더미 집계 평가기의 경계와 미완료 처리를 메모리 자료로 검증한다."""

import copy
import unittest
from decimal import Decimal, localcontext

from excel_parser.benchmark.manifest import score_manifest


def fixture() -> tuple[list[dict], dict]:
    """원본 생성 계약의 열 주소를 명시한 한 행짜리 정답과 예측을 만든다.

    Args:
        없음.
    Returns:
        117개 예측 레코드와 독립 manifest 항목.
    Raises:
        없음.
    Side Effects:
        메모리 객체만 생성하며 파일·네트워크에 접근하지 않는다.
    """
    periods = {name: {"monthly": [1] * 12, "annual": 12}
               for name in ("prior", "plan", "outlook", "next_plan")}
    entry = {
        "filename": "synthetic.xlsx",
        "expected": {
            "revenue": {"records": 1, "periods": copy.deepcopy(periods)},
            "cost": {"records": 1, "periods": copy.deepcopy(periods),
                     "next_projection": {"monthly": [1] * 12, "annual": 12}},
        },
    }
    columns = {
        ("매출_세부_IFRS15後", 5): [
            "H I J K L M N O P Q R S T",
            "V W X Y Z AA AB AC AD AE AF AG AH",
            "AJ AK AL AM AN AO AP AQ AR AS AT AU AV",
            "AX AY AZ BA BB BC BD BE BF BG BH BI BJ",
        ],
        ("OpEx_세부_IFRS15後", 6): [
            "Q R S T U V W X Y Z AA AB AC",
            "AE AF AG AH AI AJ AK AL AM AN AO AP AQ",
            "AS AT AU AV AW AX AY AZ BA BB BC BD BE",
            "BG BH BI BJ BK BL BM BN BO BP BQ BR BS",
            "BU BV BW BX BY BZ CA CB CC CD CE CF CG",
        ],
    }
    records = []
    for (sheet, row), ranges in columns.items():
        for column_range in ranges:
            for offset, column in enumerate(column_range.split()):
                records.append({
                    "source": {"sheet": sheet, "cell": f"{column}{row}"},
                    "value": {"status": "confirmed", "value_kind": "amount",
                              "normalized_value": Decimal(12000 if offset == 0 else 1000)},
                    "mapping": {"status": "partial"},
                })
    return records, entry


class ManifestScoreTests(unittest.TestCase):
    """고정된 기본 더미 계약과 예측 품질의 분리를 검증한다."""

    def test_complete_amounts_allow_partial_mapping_without_claiming_semantic_accuracy(self):
        """금액이 맞는 partial 매핑을 별도로 보고하는지 확인한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 결과나 입력 불변 조건이 다르면 발생한다.
        Side Effects:
            메모리 fixture만 사용한다.
        """
        records, entry = fixture()
        before = copy.deepcopy((records, entry))
        result = score_manifest(records, entry)
        self.assertEqual(result["groups_total"], 117)
        self.assertEqual(result["complete_groups"], 117)
        self.assertEqual(result["matching_groups"], 117)
        self.assertEqual(result["match_rate"], 1.0)
        self.assertEqual(result["mapping_status_counts"], {"partial": 117})
        self.assertFalse(result["semantic_accuracy_evaluated"])
        self.assertEqual(len(result["details"]), 100)
        self.assertEqual(result["details_truncated"], 17)
        self.assertEqual((records, entry), before)

    def test_empty_predictions_keep_all_required_groups_in_denominator(self):
        """예측이 없을 때 완전성과 일치율이 모두 0인지 확인한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 누락 집계가 잘못되면 발생한다.
        Side Effects:
            없음.
        """
        _, entry = fixture()
        result = score_manifest([], entry)
        self.assertEqual(result["groups_total"], 117)
        self.assertEqual(result["missing_cells"], 117)
        self.assertEqual(result["complete_groups"], 0)
        self.assertEqual(result["match_rate"], 0.0)

    def test_multiple_rows_are_summed_without_using_header_subtotals(self):
        """상단 소계 대신 모든 지정 상세행을 더하는지 확인한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 상세행 범위나 합계가 잘못되면 발생한다.
        Side Effects:
            메모리 객체만 변경한다.
        """
        records, entry = fixture()
        extra = copy.deepcopy(records)
        for record in extra:
            address = record["source"]["cell"]
            record["source"]["cell"] = address[:-1] + str(int(address[-1]) + 1)
            record["value"]["normalized_value"] *= 2
        records.extend(extra)
        for section in entry["expected"].values():
            section["records"] = 2
            aggregates = list(section["periods"].values())
            if "next_projection" in section:
                aggregates.append(section["next_projection"])
            for aggregate in aggregates:
                aggregate["monthly"] = [3] * 12
                aggregate["annual"] = 36
        records.append({"source": {"sheet": "매출_세부_IFRS15後", "cell": "H2"},
                        "value": {"status": "confirmed", "value_kind": "amount", "normalized_value": "999999"}})
        result = score_manifest(records, entry)
        self.assertEqual(result["required_cells"], 234)
        self.assertEqual(result["matching_groups"], 117)

    def test_missing_duplicate_unresolved_and_non_amount_are_incomplete(self):
        """서로 다른 불완전 예측 원인을 셀 좌표 기준으로 세는지 확인한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 미완료 그룹이나 원인 집계가 틀리면 발생한다.
        Side Effects:
            메모리 fixture만 변경한다.
        """
        records, entry = fixture()
        records = [record for record in records if record["source"] != {"sheet": "매출_세부_IFRS15後", "cell": "I5"}]
        indexed = {(r["source"]["sheet"], r["source"]["cell"]): r for r in records}
        records.extend([copy.deepcopy(indexed[("OpEx_세부_IFRS15後", "Q6")])] * 2)
        indexed[("OpEx_세부_IFRS15後", "R6")]["value"].update(status="unresolved", normalized_value=None)
        indexed[("OpEx_세부_IFRS15後", "S6")]["value"]["value_kind"] = "ratio"
        result = score_manifest(records, entry)
        self.assertEqual(result["missing_cells"], 1)
        self.assertEqual(result["duplicate_cells"], 1)
        self.assertEqual(result["unresolved_cells"], 2)
        self.assertEqual(result["complete_groups"], 113)
        self.assertEqual(result["matching_groups"], 113)
        self.assertIsNone(result["details"][0]["actual_won"])

    def test_invalid_prediction_amounts_do_not_abort_evaluation(self):
        """잘못된 예측 금액이 전체 평가를 중단하지 않는지 확인한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 오류 금액이 통과하거나 예외가 발생하면 실패한다.
        Side Effects:
            메모리 fixture만 변경한다.
        """
        for bad in (None, "NaN", "Infinity", Decimal("-Infinity"), float("nan"), True, "금액 아님"):
            with self.subTest(value=str(bad)):
                records, entry = fixture()
                records[1]["value"]["normalized_value"] = bad
                result = score_manifest(records, entry)
                self.assertEqual(result["invalid_value_cells"], 1)
                self.assertEqual(result["unresolved_cells"], 1)
                self.assertEqual(result["matching_groups"], 116)

    def test_wrong_finite_amount_is_complete_but_does_not_match(self):
        """금액 오차와 예측 누락을 구분하는지 확인한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 완전성 또는 일치율이 잘못되면 발생한다.
        Side Effects:
            메모리 fixture만 변경한다.
        """
        records, entry = fixture()
        records[1]["value"]["normalized_value"] = "1000.0001"
        result = score_manifest(records, entry)
        self.assertEqual(result["complete_groups"], 117)
        self.assertEqual(result["matching_groups"], 116)
        self.assertEqual(result["match_rate"], 116 / 117)
        self.assertEqual(result["details"][0]["actual_won"], "1000.0001")

    def test_production_unresolved_value_kind_null_is_a_valid_prediction(self):
        """AI 미분류 레코드의 null value_kind를 미확정으로 세는지 확인한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 정상 미분류 레코드가 거부되거나 확정되면 발생한다.
        Side Effects:
            메모리 fixture만 변경한다.
        """
        for status in ("unresolved", "confirmed"):
            with self.subTest(status=status):
                records, entry = fixture()
                records[1]["value"] = {
                    "original_value": "1", "normalized_value": None,
                    "status": status, "value_kind": None, "currency": None,
                    "source_unit": None, "multiplier": None,
                    "rule_ids": ["AI_NOT_CLASSIFIED"],
                    "diagnostics": ["LLM이 이 후보 셀을 분류하지 않음"],
                }
                records[1]["mapping"] = {"status": "unmapped"}
                result = score_manifest(records, entry)
                self.assertEqual(result["unresolved_cells"], 1)
                self.assertEqual(result["invalid_value_cells"], 0)
                self.assertEqual(result["complete_groups"], 116)

    def test_exact_sum_preserves_small_remainder_after_large_cancellation(self):
        """큰 양수·음수 사이의 작은 금액이 반올림되지 않는지 확인한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 기본 정밀도로 소액이 사라지면 발생한다.
        Side Effects:
            지역 Decimal context와 메모리 fixture만 변경한다.
        """
        original, entry = fixture()
        records = []
        for record in original:
            annual = record["value"]["normalized_value"] == 12000
            for offset, amount in enumerate(("1e50", "1.2" if annual else "0.1", "-1e50")):
                copied = copy.deepcopy(record)
                address = copied["source"]["cell"]
                copied["source"]["cell"] = address[:-1] + str(int(address[-1]) + offset)
                copied["value"]["normalized_value"] = amount
                records.append(copied)
        for section in entry["expected"].values():
            section["records"] = 3
            aggregates = list(section["periods"].values())
            if "next_projection" in section:
                aggregates.append(section["next_projection"])
            for aggregate in aggregates:
                aggregate.update(monthly=["0.0001"] * 12, annual="0.0012")
        with localcontext() as context:
            context.prec = 6
            result = score_manifest(records, entry)
            self.assertEqual(context.prec, 6)
        self.assertEqual(result["matching_groups"], 117)

    def test_gold_unit_conversion_preserves_more_than_28_digits(self):
        """정답의 천원 환산에서도 긴 소수 금액이 보존되는지 확인한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 정답 환산이 반올림되면 발생한다.
        Side Effects:
            지역 Decimal context와 메모리 fixture만 변경한다.
        """
        records, entry = fixture()
        entry["expected"]["revenue"]["periods"]["prior"]["monthly"][0] = "123456789012345678901234567.0001"
        records[1]["value"]["normalized_value"] = "123456789012345678901234567000.1"
        with localcontext() as context:
            context.prec = 6
            result = score_manifest(records, entry)
        self.assertEqual(result["matching_groups"], 117)

    def test_cost_next_projection_is_evaluated_and_failed_details_are_first(self):
        """별도 next_projection 그룹이 상세 제한 밖으로 숨지 않는지 확인한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 마지막 비용 기간을 빠뜨리면 발생한다.
        Side Effects:
            메모리 fixture만 변경한다.
        """
        records, entry = fixture()
        records = [r for r in records if not (r["source"]["sheet"] == "OpEx_세부_IFRS15後" and r["source"]["cell"] == "CG6")]
        result = score_manifest(records, entry)
        detail = result["details"][0]
        self.assertEqual((detail["section"], detail["period"], detail["month"]), ("cost", "next_projection", 12))
        self.assertEqual(detail["range"], "CG6:CG6")
        self.assertEqual(result["matching_groups"], 116)

    def test_invalid_gold_and_record_structure_are_rejected(self):
        """정답 오류와 레코드 외곽 계약 오류는 명시적으로 거부하는지 확인한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 잘못된 계약이 허용되면 발생한다.
        Side Effects:
            메모리 fixture만 변경한다.
        """
        for bad in (None, True, "NaN", float("inf"), "금액 아님"):
            with self.subTest(gold=str(bad)):
                records, entry = fixture()
                entry["expected"]["revenue"]["periods"]["prior"]["monthly"][0] = bad
                with self.assertRaises(ValueError):
                    score_manifest(records, entry)
        records, entry = fixture()
        for bad_records in (None, {}, [None], [{"source": {}, "value": {}}]):
            with self.subTest(records=bad_records), self.assertRaises(ValueError):
                score_manifest(bad_records, entry)
        for bad_entry in ({}, {"filename": "x", "expected": []}):
            with self.subTest(entry=bad_entry), self.assertRaises(ValueError):
                score_manifest(records, bad_entry)
        entry["expected"]["cost"]["records"] = True
        with self.assertRaises(ValueError):
            score_manifest(records, entry)


if __name__ == "__main__":
    unittest.main()
