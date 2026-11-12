"""기존 응답 프로토콜을 유지하는 메모리 캐시와 디스크 호환 경계를 검사한다."""

import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from excel_parser_ai.client import ResponsesClient


def response_fixture() -> dict:
    """실제 API 외곽 형식을 따르는 작은 구조화 응답을 만든다.

    Args:
        없음.
    Returns:
        JSON 출력 문자열과 사용량을 가진 응답.
    Raises:
        없음.
    Side Effects:
        메모리 객체만 생성한다.
    """
    return {"status": "completed", "output": [{"content": [{"type": "output_text", "text": '{"answer": 1}'}]}],
            "usage": {"input_tokens": 10, "output_tokens": 2}}


class BenchmarkCacheTests(unittest.TestCase):
    """디스크 없이 응답 캐시 적중·갱신·기존 저장 경로를 검증한다."""

    def test_memory_cache_isolated_from_returned_mutable_data(self) -> None:
        """메모리 캐시가 디스크를 조회하지 않고 반환값 변경에 오염되지 않는다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 저장소 접근 또는 캐시 오염이 발생한 경우.
        Side Effects:
            대체 전송과 메모리 캐시만 사용한다.
        """
        transport = Mock(return_value=response_fixture())
        client = ResponsesClient("/not-created", transport=transport, response_cache={})
        with patch.object(Path, "is_file", side_effect=AssertionError("disk read")), patch.object(Path, "mkdir", side_effect=AssertionError("disk write")):
            first = client.ask("test", "prompt", {}, {})
            first["answer"] = 99
            second = client.ask("test", "prompt", {}, {})
        self.assertEqual(second, {"answer": 1})
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(client.call_count, 1)
        self.assertTrue(client.events[-1]["cached"])

    def test_refresh_and_model_change_do_not_reuse_old_response(self) -> None:
        """명시적 갱신과 모델 변경이 별도 실제 요청을 발생시킨다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 다른 실행 조건의 응답이 잘못 재사용된 경우.
        Side Effects:
            대체 전송과 메모리 상태만 변경한다.
        """
        transport = Mock(return_value=response_fixture())
        client = ResponsesClient("/not-created", transport=transport, response_cache={})
        client.ask("test", "prompt", {}, {})
        client.ask("test", "prompt", {}, {}, refresh=True)
        client.model = "different-model"
        client.ask("test", "prompt", {}, {})
        self.assertEqual(transport.call_count, 3)

    def test_default_disk_cache_read_protocol_is_preserved(self) -> None:
        """메모리 옵션을 주지 않은 기존 호출의 캐시 읽기를 유지한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 기존 디스크 캐시 응답 형식이 달라진 경우.
        Side Effects:
            디스크 읽기를 메모리 JSON으로 대체한다.
        """
        transport = Mock(side_effect=AssertionError("network"))
        client = ResponsesClient("/not-created", transport=transport)
        with patch.object(Path, "is_file", return_value=True), patch.object(Path, "read_text", return_value=json.dumps({"data": {"answer": 7}})):
            result = client.ask("test", "prompt", {}, {})
        self.assertEqual(result, {"answer": 7})
        transport.assert_not_called()

    def test_default_disk_cache_write_protocol_is_preserved(self) -> None:
        """기존 디스크 저장 분기를 실제 파일 없이 검사한다.

        Args:
            없음.
        Returns:
            없음.
        Raises:
            AssertionError: 기존 응답 envelope나 교체 저장 순서가 잘못된 경우.
        Side Effects:
            디스크 변경 함수를 모두 대체한다.
        """
        client = ResponsesClient("/not-created", transport=Mock(return_value=response_fixture()))
        with patch.object(Path, "is_file", return_value=False), patch.object(Path, "mkdir") as mkdir, patch.object(Path, "write_text") as write, patch.object(Path, "replace") as replace:
            client.ask("test", "prompt", {}, {})
        mkdir.assert_called_once()
        replace.assert_called_once()
        self.assertEqual(json.loads(write.call_args.args[0])["data"], {"answer": 1})
