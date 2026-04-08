"""
Tests for pure helper functions in server.py.
These functions are tested directly without starting the MCP server or hitting the API.
"""

import pytest
from mcp.types import GetPromptResult

from server import (
    _build_prompt_list,
    _format_qx_scores,
    _format_sessions,
    _get_prompt_result,
    _parse_vtt,
)


# ---------------------------------------------------------------------------
# _build_prompt_list
# ---------------------------------------------------------------------------


def test_build_prompt_list_returns_two_prompts():
    prompts = _build_prompt_list()
    assert len(prompts) == 2


def test_build_prompt_list_names():
    names = {p.name for p in _build_prompt_list()}
    assert names == {"analyze-test", "summarize-test"}


def test_analyze_test_has_required_test_id_arg():
    prompt = next(p for p in _build_prompt_list() if p.name == "analyze-test")
    arg = next(a for a in prompt.arguments if a.name == "test_id")
    assert arg.required is True


def test_summarize_test_has_optional_audience_arg():
    prompt = next(p for p in _build_prompt_list() if p.name == "summarize-test")
    arg = next(a for a in prompt.arguments if a.name == "audience")
    assert arg.required is False


# ---------------------------------------------------------------------------
# _get_prompt_result
# ---------------------------------------------------------------------------


def test_analyze_test_prompt_result_contains_test_id():
    result = _get_prompt_result("analyze-test", {"test_id": "abc-123"})
    text = result.messages[0].content.text
    assert "abc-123" in text


def test_analyze_test_prompt_result_mentions_tools():
    result = _get_prompt_result("analyze-test", {"test_id": "abc-123"})
    text = result.messages[0].content.text
    assert "list_sessions" in text
    assert "get_qx_scores" in text
    assert "get_session_details" in text
    assert "get_transcript" in text


def test_analyze_test_requires_test_id():
    with pytest.raises(ValueError, match="test_id is required"):
        _get_prompt_result("analyze-test", {})


def test_analyze_test_requires_test_id_when_none():
    with pytest.raises(ValueError, match="test_id is required"):
        _get_prompt_result("analyze-test", None)


def test_summarize_test_prompt_result_contains_test_id():
    result = _get_prompt_result("summarize-test", {"test_id": "xyz-456"})
    text = result.messages[0].content.text
    assert "xyz-456" in text


def test_summarize_test_defaults_audience_to_stakeholders():
    result = _get_prompt_result("summarize-test", {"test_id": "xyz-456"})
    text = result.messages[0].content.text
    assert "stakeholders" in text


def test_summarize_test_uses_custom_audience():
    result = _get_prompt_result(
        "summarize-test", {"test_id": "xyz-456", "audience": "executives"}
    )
    text = result.messages[0].content.text
    assert "executives" in text


def test_summarize_test_requires_test_id():
    with pytest.raises(ValueError, match="test_id is required"):
        _get_prompt_result("summarize-test", {})


def test_unknown_prompt_raises():
    with pytest.raises(ValueError, match="Unknown prompt"):
        _get_prompt_result("nonexistent-prompt", {})


def test_analyze_test_returns_get_prompt_result():
    result = _get_prompt_result("analyze-test", {"test_id": "abc-123"})
    assert isinstance(result, GetPromptResult)
    assert len(result.messages) == 1
    assert result.messages[0].role == "user"


def test_summarize_test_returns_get_prompt_result():
    result = _get_prompt_result("summarize-test", {"test_id": "xyz-456"})
    assert isinstance(result, GetPromptResult)
    assert len(result.messages) == 1
    assert result.messages[0].role == "user"


# ---------------------------------------------------------------------------
# _format_sessions
# ---------------------------------------------------------------------------


def test_format_sessions_empty():
    assert _format_sessions([]) == "No sessions found."


def test_format_sessions_includes_session_id():
    sessions = [
        {
            "sessionId": "sess-abc-123",
            "status": "complete",
            "startTime": "2024-01-01T10:00:00Z",
            "finishTime": "2024-01-01T10:30:00Z",
        }
    ]
    result = _format_sessions(sessions)
    assert "sess-abc-123" in result
    assert "complete" in result


def test_format_sessions_shows_total():
    sessions = [{"sessionId": f"s{i}", "status": "complete"} for i in range(3)]
    result = _format_sessions(sessions)
    assert "3 session(s)" in result


# ---------------------------------------------------------------------------
# _format_qx_scores
# ---------------------------------------------------------------------------


def test_format_qx_scores_no_scores():
    data = {"testId": "t-001", "meta": {"totalQxTasks": 2, "completes": 10}, "qxScores": []}
    result = _format_qx_scores(data)
    assert "No QX scores available." in result
    assert "t-001" in result


def test_format_qx_scores_includes_score():
    data = {
        "testId": "t-001",
        "meta": {"totalQxTasks": 1, "completes": 5},
        "qxScores": [
            {
                "taskGroupId": "tg-1",
                "label": "Task 1",
                "qxScore": 78,
                "components": {"behavioral": 80, "attitudinal": 76},
                "values": {},
            }
        ],
    }
    result = _format_qx_scores(data)
    assert "78/100" in result
    assert "Task 1" in result
    assert "80" in result


# ---------------------------------------------------------------------------
# _parse_vtt
# ---------------------------------------------------------------------------


SAMPLE_VTT = """\
WEBVTT

00:00:01.000 --> 00:00:03.500
Hello, I'm going to walk through this page.

00:00:03.800 --> 00:00:06.200
The navigation looks a bit confusing to me.

00:00:10.000 --> 00:00:12.000
I would expect the button to be here.
"""


def test_parse_vtt_extracts_text():
    result = _parse_vtt(SAMPLE_VTT)
    assert "Hello, I'm going to walk through this page." in result
    assert "The navigation looks a bit confusing to me." in result
    assert "I would expect the button to be here." in result


def test_parse_vtt_formats_timestamps():
    result = _parse_vtt(SAMPLE_VTT)
    assert "[0:00:01]" in result
    assert "[0:00:03]" in result
    assert "[0:00:10]" in result


def test_parse_vtt_strips_webvtt_header():
    result = _parse_vtt(SAMPLE_VTT)
    assert "WEBVTT" not in result


def test_parse_vtt_empty_returns_placeholder():
    result = _parse_vtt("WEBVTT\n\n")
    assert result == "(No transcript content)"


def test_parse_vtt_multiline_cue_joined():
    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nLine one\nLine two\n"
    result = _parse_vtt(vtt)
    assert "Line one Line two" in result


def test_parse_vtt_hour_formatting():
    vtt = "WEBVTT\n\n01:23:45.000 --> 01:23:47.000\nSome text\n"
    result = _parse_vtt(vtt)
    assert "[1:23:45]" in result
