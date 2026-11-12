"""기본 더미 manifest의 월·연간 금액 합계만 독립적으로 평가한다.

이 모듈은 파서의 의미 매핑·정규화 함수를 사용하지 않는다. 기준은
baseline_strict_native_input_only_v1 생성 계약이며 상세 의미 정확도는 평가하지
않는다. manifest와 그 정답은 파서나 모델 입력에 전달하지 않아야 한다.
"""

import re
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation, MAX_EMAX, MIN_EMIN, localcontext
from typing import Any


MANIFEST_VERSION = "baseline_strict_native_input_only_v1"
_PERIODS = ("prior", "plan", "outlook", "next_plan")
_LAYOUTS = (
    ("revenue", "매출_세부_IFRS15後", 5, (8, 22, 36, 50)),
    ("cost", "OpEx_세부_IFRS15後", 6, (17, 31, 45, 59, 73)),
)
_ADDRESS = re.compile(r"^[A-Z]+[1-9][0-9]*$")
_DETAIL_LIMIT = 100


def _amount(value: Any) -> Decimal:
    """금액을 유한한 Decimal로 변환한다.

    Args:
        value: 정수·실수·문자열·Decimal 금액. bool과 None은 허용하지 않는다.
    Returns:
        유한한 Decimal 금액.
    Raises:
        ValueError: 숫자가 아니거나 비유한 금액인 경우.
    Side Effects:
        없음. 파일 접근과 입력 변경을 하지 않는다.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError("금액은 유한한 숫자여야 합니다")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError("금액은 유한한 숫자여야 합니다") from error
    if not number.is_finite():
        raise ValueError("비유한 금액은 허용하지 않습니다")
    return number


def _column(number: int) -> str:
    """1부터 시작하는 열 번호를 Excel 열 문자로 바꾼다.

    Args:
        number: 생성 계약의 양수 열 번호.
    Returns:
        A, Z, AA 형태의 열 문자.
    Raises:
        없음. 내부의 양수 열 상수만 전달한다.
    Side Effects:
        없음.
    """
    letters = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _exact_sum(values: list[Decimal]) -> Decimal:
    """입력 자릿수와 지수 범위를 보존하는 정밀도로 금액을 합산한다.

    Args:
        values: 유한한 Decimal 금액 목록.
    Returns:
        기본 Decimal 정밀도에 의해 반올림되지 않은 합계.
    Raises:
        ValueError: Decimal 구현이 지원하는 정밀도 범위를 넘는 경우.
    Side Effects:
        지역 Decimal context만 변경하며 호출자의 context는 유지한다.
    """
    if not values:
        return Decimal(0)
    minimum_exponent = min(value.as_tuple().exponent for value in values)
    maximum_adjusted = max(value.adjusted() for value in values)
    precision = max(1, maximum_adjusted - minimum_exponent + 1) + len(str(len(values))) + 1
    with localcontext() as context:
        context.prec = precision
        context.Emax = MAX_EMAX
        context.Emin = MIN_EMIN
        return sum(values, Decimal(0))


def _group_specs(entry: dict) -> list[dict]:
    """기본 더미 생성 계약의 117개 평가 그룹을 만든다.

    Args:
        entry: manifest files의 단일 항목. 금액의 원래 단위는 천원이다.
    Returns:
        원본 셀 범위와 원 단위 기대 합계를 가진 그룹 목록.
    Raises:
        ValueError: manifest 항목의 구조·행수·금액이 잘못된 경우.
    Side Effects:
        없음. 정답을 입력 파서에 전달하거나 파일을 읽지 않는다.
    """
    if not isinstance(entry, dict) or not isinstance(entry.get("filename"), str) or not entry["filename"]:
        raise ValueError("manifest 항목에는 filename 문자열이 필요합니다")
    expected = entry.get("expected")
    if not isinstance(expected, dict):
        raise ValueError("manifest expected는 객체여야 합니다")
    groups = []
    for section, sheet, first_row, annual_columns in _LAYOUTS:
        values = expected.get(section)
        if not isinstance(values, dict):
            raise ValueError(f"expected.{section}는 객체여야 합니다")
        count = values.get("records")
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            raise ValueError(f"expected.{section}.records는 양수 정수여야 합니다")
        periods = values.get("periods")
        if not isinstance(periods, dict):
            raise ValueError(f"expected.{section}.periods는 객체여야 합니다")
        names = _PERIODS + (("next_projection",) if section == "cost" else ())
        for period, annual_column in zip(names, annual_columns):
            aggregate = values.get(period) if period == "next_projection" else periods.get(period)
            if not isinstance(aggregate, dict):
                raise ValueError(f"{section}.{period}는 객체여야 합니다")
            monthly = aggregate.get("monthly")
            if not isinstance(monthly, list) or len(monthly) != 12:
                raise ValueError(f"{section}.{period}.monthly는 12개 금액 목록이어야 합니다")
            amounts = [aggregate.get("annual"), *monthly]
            for month, raw_amount in enumerate(amounts):
                # 천원에서 원으로의 변환도 Decimal 기본 정밀도에 의존하지 않는다.
                sign, digits, exponent = _amount(raw_amount).as_tuple()
                expected_won = Decimal((sign, digits, exponent + 3))
                groups.append({
                    "section": section,
                    "period": period,
                    "aggregation": "annual" if month == 0 else "month",
                    "month": month or None,
                    "sheet": sheet,
                    "column": _column(annual_column + month),
                    "first_row": first_row,
                    "record_count": count,
                    "expected_won": expected_won,
                })
    return groups


def _index_records(records: list) -> dict[tuple[str, str], list[dict]]:
    """단일 입력 파일의 예측을 원본 시트·셀별로 모은다.

    Args:
        records: 단일 파일에 대한 표준 레코드 목록.
    Returns:
        (시트, 셀)마다 모든 예측을 보존한 색인. 중복을 덮어쓰지 않는다.
    Raises:
        ValueError: 목록·레코드·출처·값 객체의 구조가 잘못된 경우.
    Side Effects:
        없음. 원본 레코드를 읽기 참조하며 변경하지 않는다.
    """
    if not isinstance(records, list):
        raise ValueError("records는 목록이어야 합니다")
    indexed = defaultdict(list)
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("각 record는 객체여야 합니다")
        source = record.get("source")
        value = record.get("value")
        if not isinstance(source, dict) or not isinstance(value, dict):
            raise ValueError("record.source와 record.value는 객체여야 합니다")
        sheet, cell = source.get("sheet"), source.get("cell")
        if not isinstance(sheet, str) or not sheet or not isinstance(cell, str) or not _ADDRESS.fullmatch(cell):
            raise ValueError("record.source에는 시트 이름과 유효한 셀 주소가 필요합니다")
        if (not isinstance(value.get("status"), str) or "value_kind" not in value
                or (value["value_kind"] is not None and not isinstance(value["value_kind"], str))):
            raise ValueError("record.value에는 status 문자열과 문자열 또는 null인 value_kind가 필요합니다")
        mapping = record.get("mapping", {})
        if not isinstance(mapping, dict) or ("status" in mapping and not isinstance(mapping["status"], str)):
            raise ValueError("record.mapping은 status 문자열을 가진 객체여야 합니다")
        indexed[(sheet, cell)].append(record)
    return indexed


def score_manifest(records: list, entry: dict) -> dict:
    """예측 금액을 기본 더미 manifest의 월·연간 합계와 대조한다.

    Args:
        records: 단일 파일의 표준 레코드 목록. 정답 정보가 주입되지 않은 예측.
        entry: baseline_strict_native_input_only_v1 manifest files의 해당 항목.
    Returns:
        117개 그룹의 완전성·금액 일치율과 셀별 누락·중복·미확정 개수.
        match_rate의 분모에는 미완료 그룹도 포함한다. details는 실패를 먼저
        최대 100개 담는다. duplicate_cells는 추가 레코드 수가 아닌 중복된
        필수 셀 좌표 수다. mapping 상태는 별도 집계하며 의미 정확도가 아니다.
    Raises:
        ValueError: manifest 정답 또는 레코드 객체 구조가 잘못된 경우.
        잘못된 예측 금액은 예외 대신 invalid_value_cells로 집계한다.
    Side Effects:
        없음. 파일·캐시·네트워크 접근과 입력 객체 변경을 하지 않는다.
    """
    groups = _group_specs(entry)
    indexed = _index_records(records)
    totals = Counter()
    mapping_statuses = Counter()
    details = []
    for group in groups:
        counts = Counter()
        amounts = []
        for row in range(group["first_row"], group["first_row"] + group["record_count"]):
            predictions = indexed.get((group["sheet"], f"{group['column']}{row}"), [])
            for prediction in predictions:
                mapping_statuses[prediction.get("mapping", {}).get("status", "missing")] += 1
            if not predictions:
                counts["missing_cells"] += 1
            elif len(predictions) != 1:
                counts["duplicate_cells"] += 1
            else:
                value = predictions[0]["value"]
                if value["status"] != "confirmed" or value["value_kind"] != "amount":
                    counts["unresolved_cells"] += 1
                    continue
                try:
                    amounts.append(_amount(value.get("normalized_value")))
                except ValueError:
                    counts["unresolved_cells"] += 1
                    counts["invalid_value_cells"] += 1
        complete = not any(counts[key] for key in ("missing_cells", "duplicate_cells", "unresolved_cells"))
        observed = _exact_sum(amounts) if complete else None
        matches = complete and observed == group["expected_won"]
        totals.update(counts)
        totals["complete_groups"] += int(complete)
        totals["matching_groups"] += int(matches)
        details.append({
            "section": group["section"], "period": group["period"],
            "aggregation": group["aggregation"], "month": group["month"],
            "sheet": group["sheet"],
            "range": f"{group['column']}{group['first_row']}:{group['column']}{group['first_row'] + group['record_count'] - 1}",
            "required_cells": group["record_count"],
            "expected_won": format(group["expected_won"], "f"),
            "actual_won": format(observed, "f") if complete else None,
            "complete": complete, "matches": matches,
            **{key: counts[key] for key in ("missing_cells", "duplicate_cells", "unresolved_cells", "invalid_value_cells")},
        })
    details.sort(key=lambda detail: detail["matches"])
    return {
        "manifest_version": MANIFEST_VERSION,
        "filename": entry["filename"],
        "evaluation_scope": "aggregate_amounts_only",
        "semantic_accuracy_evaluated": False,
        "groups_total": len(groups),
        "complete_groups": totals["complete_groups"],
        "matching_groups": totals["matching_groups"],
        "match_rate": totals["matching_groups"] / len(groups),
        "required_cells": sum(group["record_count"] for group in groups),
        **{key: totals[key] for key in ("missing_cells", "duplicate_cells", "unresolved_cells", "invalid_value_cells")},
        "mapping_status_counts": dict(mapping_statuses),
        "details": details[:_DETAIL_LIMIT],
        "details_truncated": max(0, len(details) - _DETAIL_LIMIT),
    }
