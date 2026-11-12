"""독립 셀 정답과 표준 레코드를 메모리에서 비교하는 공통 평가 계약."""

from collections import defaultdict
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath
import re
from typing import Any


SourceKey = tuple[str, str, str]
SEMANTIC_FIELDS = ("organization_code", "year", "month", "aggregation", "scenario")
OPTIONAL_FIELDS = ("item_code", "item_name", "source_unit")
REQUIRED_FIELDS = (
    "source_file", "sheet", "cell", *SEMANTIC_FIELDS, "normalized_value"
)
DETAIL_LIMIT = 100


def _source_key(source_file: Any, sheet: Any, cell: Any) -> SourceKey | None:
    """파일 기본명과 시트·셀 좌표를 평가용 출처 키로 묶는다.

    Args:
        source_file: 파일 기본명 또는 원본 경로 문자열.
        sheet: 원본 시트명 문자열.
        cell: 대문자 A1 형식 원본 셀 주소.
    Returns:
        유효한 출처 키. 빈 값이나 잘못된 좌표이면 None.
    Raises:
        없음.
    Side Effects:
        없음. 경로를 해석만 하며 파일 시스템에 접근하지 않는다.
    """
    if not all(isinstance(value, str) and value.strip() for value in (source_file, sheet, cell)):
        return None
    portable_path = source_file.replace("\\", "/")
    if portable_path.endswith("/") or not re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]*", cell):
        return None
    filename = PurePosixPath(portable_path).name
    if filename in {"", ".", ".."}:
        return None
    return filename, sheet, cell


def _decimal(value: Any) -> Decimal | None:
    """유한한 숫자 표현을 정확한 십진수 비교 값으로 변환한다.

    Args:
        value: 숫자, Decimal 또는 숫자 문자열.
    Returns:
        유한 Decimal. 빈 값·불리언·비숫자·비유한 값이면 None.
    Raises:
        없음.
    Side Effects:
        없음. Decimal 전역 정밀도나 입력 객체를 변경하지 않는다.
    """
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _section(record: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    """표준 레코드의 하위 객체를 읽고 잘못된 형식은 빈 조회 객체로 취급한다.

    Args:
        record: 검사할 표준 레코드.
        name: source, organization, item, period, value, mapping 등의 이름.
    Returns:
        원래 하위 매핑 또는 새 빈 사전.
    Raises:
        없음.
    Side Effects:
        없음. 반환한 원래 하위 객체도 수정하지 않는다.
    """
    section = record.get(name)
    return section if isinstance(section, Mapping) else {}


def _truth_index(truth_rows: Iterable[Mapping[str, Any]]) -> dict[SourceKey, dict[str, Any]]:
    """독립 정답 행을 검사하고 출처별 복사본과 Decimal 정답을 만든다.

    Args:
        truth_rows: 필수 출처·의미 필드·정규화값을 가진 독립 정답 행들.
    Returns:
        출처 키별 정답 사전. 선택 필드는 제공된 경우에만 포함한다.
    Raises:
        ValueError: 필수 필드 누락, 무효 출처·금액 또는 중복 정답인 경우.
    Side Effects:
        입력 반복자를 소비한다. 원본 행이나 파일은 변경하지 않는다.
    """
    indexed = {}
    for position, row in enumerate(truth_rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"정답 {position}: 객체가 필요합니다")
        missing = [field for field in REQUIRED_FIELDS if field not in row]
        if missing:
            raise ValueError(f"정답 {position}: 필수 필드 누락 {', '.join(missing)}")
        key = _source_key(row["source_file"], row["sheet"], row["cell"])
        if key is None:
            raise ValueError(f"정답 {position}: 파일·시트·셀 출처가 유효하지 않습니다")
        if key in indexed:
            raise ValueError(f"정답 {position}: 중복 출처 {key}")
        value = _decimal(row["normalized_value"])
        if value is None:
            raise ValueError(f"정답 {position}: normalized_value는 유한 숫자여야 합니다")
        indexed[key] = {
            **{field: row[field] for field in SEMANTIC_FIELDS},
            **{field: row[field] for field in OPTIONAL_FIELDS if field in row},
            "normalized_value": value,
        }
    return indexed


def _compare_record(record: Mapping[str, Any], truth: Mapping[str, Any]) -> dict[str, Any]:
    """유일한 출처에 연결된 표준 레코드의 의미·금액·확정 상태를 비교한다.

    Args:
        record: 중첩된 공통 표준 레코드.
        truth: 유효성 검사와 Decimal 변환을 마친 정답.
    Returns:
        의미·금액 상태, 불일치 필드 및 전체 정답 여부.
    Raises:
        없음.
    Side Effects:
        없음. 원본 레코드나 정답 객체를 변경하지 않는다.
    """
    organization = _section(record, "organization")
    item = _section(record, "item")
    period = _section(record, "period")
    value = _section(record, "value")
    mapping = _section(record, "mapping")
    predicted = {
        "organization_code": organization.get("code"),
        "item_code": item.get("code"),
        "item_name": item.get("name"),
        "source_unit": value.get("source_unit"),
        **{field: period.get(field) for field in ("year", "month", "aggregation", "scenario")},
    }
    compared_fields = [*SEMANTIC_FIELDS, *(field for field in OPTIONAL_FIELDS if field in truth)]
    mismatches = [
        field for field in compared_fields
        if type(predicted[field]) is not type(truth[field]) or predicted[field] != truth[field]
    ]
    semantic_status = (
        "unresolved" if mapping.get("status") != "mapped"
        else "mismatch" if mismatches else "exact"
    )
    numeric = _decimal(value.get("normalized_value"))
    value_status = (
        "unresolved" if value.get("status") != "confirmed" or numeric is None
        else "exact" if numeric == truth["normalized_value"] else "mismatch"
    )
    return {
        "semantic_status": semantic_status,
        "value_status": value_status,
        "mapping_status": mapping.get("status"),
        "normalization_status": value.get("status"),
        "mismatched_fields": mismatches,
        "exact": semantic_status == value_status == "exact",
    }


def score_records(
    records: Iterable[Mapping[str, Any]], truth_rows: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    """동일 출처의 독립 셀 정답으로 누락·중복·미해결을 포함해 엄격 평가한다.

    Args:
        records: source/organization/item/period/value/mapping을 가진 표준 레코드.
            평탄한 예측 행은 지원하지 않는다. source.path의 기본명을 사용한다.
        truth_rows: source_file, sheet, cell, organization_code, year, month,
            aggregation, scenario, normalized_value를 가진 독립 정답 행.
            item_code, item_name, source_unit은 정답에 있을 때만 비교한다.
            서로 다른 입력 파일은 같은 기본명을 사용하지 않아야 한다.
    Returns:
        정답·예측 수, 출처/의미/금액별 계수와 정확도 및 최대 100개 상세.
        accuracy는 exact_match / total_expected의 0~1 비율이며 정답이 0개면
        None이다. 누락·중복 출처는 정답 처리하지 않는다. extra_sources와
        invalid_records는 별도 표시하며 정답 분모를 바꾸지 않는다.
        partial 레코드의 확정 금액은 value_exact에만 포함할 수 있다.
        details_truncated는 상한으로 생략한 상세 항목 수이다.
    Raises:
        ValueError: 독립 정답의 필수 필드·출처·금액이 무효하거나 중복인 경우.
    Side Effects:
        입력 반복자를 소비하고 새 평가 객체만 만든다. 원본 객체는 변경하지
        않으며 파일·캐시를 읽거나 쓰지 않고 API도 호출하지 않는다.
    """
    truth = _truth_index(truth_rows)
    grouped = defaultdict(list)
    invalid_positions = []
    observed_records = 0
    for position, record in enumerate(records):
        observed_records += 1
        source = _section(record, "source") if isinstance(record, Mapping) else {}
        key = _source_key(source.get("path"), source.get("sheet"), source.get("cell"))
        if key is None:
            invalid_positions.append(position)
        else:
            grouped[key].append(record)
    report = {
        "contract_version": "1.0.0",
        "evaluation_unit": "source_cell",
        "accuracy_scale": "0_to_1",
        "total_expected": len(truth),
        "observed_records": observed_records,
        "matched_unique": 0,
        "missing": 0,
        "duplicate_sources": sum(len(group) > 1 for group in grouped.values()),
        "duplicate_records": sum(len(group) - 1 for group in grouped.values()),
        "extra_sources": len(grouped.keys() - truth.keys()),
        "extra_records": sum(len(grouped[key]) for key in grouped.keys() - truth.keys()),
        "invalid_records": len(invalid_positions),
        "unresolved": 0,
        "semantic_exact": 0,
        "semantic_mismatch": 0,
        "value_exact": 0,
        "value_mismatch": 0,
        "exact_match": 0,
        "accuracy": None,
        "details": [],
        "details_truncated": 0,
    }
    for key in sorted(truth.keys() | grouped.keys()):
        group = grouped.get(key, [])
        detail = {
            "source_file": key[0], "sheet": key[1], "cell": key[2],
            "record_count": len(group),
            "duplicate": len(group) > 1,
            "semantic_status": "not_scored", "value_status": "not_scored", "exact": False,
        }
        if key not in truth:
            detail["source_status"] = "extra"
        elif not group:
            detail["source_status"] = "missing"
            report["missing"] += 1
        elif len(group) > 1:
            detail["source_status"] = "duplicate"
        else:
            detail["source_status"] = "matched"
            detail.update(_compare_record(group[0], truth[key]))
            report["matched_unique"] += 1
            report["semantic_exact"] += detail["semantic_status"] == "exact"
            report["semantic_mismatch"] += detail["semantic_status"] == "mismatch"
            report["value_exact"] += detail["value_status"] == "exact"
            report["value_mismatch"] += detail["value_status"] == "mismatch"
            report["unresolved"] += "unresolved" in (detail["semantic_status"], detail["value_status"])
            report["exact_match"] += detail["exact"]
        if len(report["details"]) < DETAIL_LIMIT:
            report["details"].append(detail)
        else:
            report["details_truncated"] += 1
    for position in invalid_positions:
        if len(report["details"]) < DETAIL_LIMIT:
            report["details"].append({
                "record_index": position, "source_status": "invalid", "record_count": 1,
                "semantic_status": "not_scored", "value_status": "not_scored", "exact": False,
            })
        else:
            report["details_truncated"] += 1
    if truth:
        report["accuracy"] = report["exact_match"] / len(truth)
    return report
