import json

from app.services.daily.daily_service import _strip_json_fence


def test_strip_json_fence_with_fence():
    raw = '```json\n{"personalized": [0, 1]}\n```'
    assert json.loads(_strip_json_fence(raw)) == {"personalized": [0, 1]}


def test_strip_json_fence_without_fence():
    raw = '{"personalized": [], "general": []}'
    assert json.loads(_strip_json_fence(raw)) == {"personalized": [], "general": []}


def test_strip_json_fence_does_not_mangle_json_leading_text():
    # Regression: lstrip("```json") would strip leading j/s/o/n chars from any text,
    # corrupting payloads whose content begins with those letters (e.g. "jsonlines...").
    raw = '{"task": "jsonlines parsing"}'
    assert _strip_json_fence(raw) == raw
    assert json.loads(_strip_json_fence(raw))["task"] == "jsonlines parsing"
