"""Regression checks for multimodal input handling."""

import os
from pathlib import Path

os.chdir(Path(__file__).parent)

from app.multimodal.provider import (
    UploadedBlob,
    build_multimodal_prompt_context,
    enhance_ops_semantics,
    extract_ops_entities,
    normalize_transcript,
    trace_events_from_context,
    validate_audio,
    validate_image,
)
from app.websocket.gateway import _coerce_multimodal_context


def test_voice_normalization_marks_ops_terms_and_write_intent() -> None:
    result = normalize_transcript("帮我重启恩金叉服务")

    assert result["normalized_transcript"] == "帮我重启nginx服务"
    assert result["corrections"][0]["to"] == "nginx"
    assert result["requires_write_confirmation"] is True


def test_multimodal_prompt_warns_agent_to_verify_with_real_tools() -> None:
    context = build_multimodal_prompt_context([
        {
            "input_type": "image",
            "summary": "截图中出现 nginx failed",
            "entities": {"services": ["nginx"], "error_keywords": ["failed"]},
            "recommended_tools": [{"tool": "get_service_status"}],
            "confidence": "medium",
        }
    ])

    assert "多模态输入识别结果" in context
    assert "必须通过真实 MCP 工具确认" in context
    assert "nginx" in context
    assert "get_service_status" in context


def test_ops_entity_extraction_and_tool_recommendations() -> None:
    text = "nginx.service failed with HTTP 502, config /etc/nginx/nginx.conf syntax error, port 80 address already in use"
    entities = extract_ops_entities(text)
    enhanced = enhance_ops_semantics({
        "input_type": "image",
        "summary": "nginx failed",
        "extracted_text": text,
        "confidence": "medium",
    })

    assert "nginx" in entities["services"]
    assert "/etc/nginx/nginx.conf" in entities["paths"]
    assert 80 in entities["ports"]
    assert "502" in entities["error_codes"]
    tools = {item["tool"] for item in enhanced["recommended_tools"]}
    display_names = {item["display_name"] for item in enhanced["recommended_tools"]}
    assert "get_service_status" in tools
    assert "get_service_logs" in tools
    assert "read_config_file" in tools
    assert "check_config_syntax" in tools
    assert "check_port" in tools
    assert "服务状态检查" in display_names


def test_low_confidence_result_requires_user_confirmation() -> None:
    enhanced = enhance_ops_semantics({
        "input_type": "image",
        "summary": "模糊截图",
        "confidence": "low",
    })

    assert enhanced["needs_user_confirmation"] is True
    assert any("识别置信度较低" in item for item in enhanced["warnings"])


def test_multimodal_trace_events_are_inferred_not_executed() -> None:
    events = trace_events_from_context([
        {
            "input_type": "audio",
            "raw_transcript": "帮我看一下恩金叉",
            "normalized_transcript": "帮我看一下 nginx",
            "corrections": [{"from": "恩金叉", "to": "nginx"}],
            "confidence": "medium",
            "provider": "aliyun_dashscope",
        }
    ])

    assert events[0]["phase"] == "voice_recognition"
    assert events[0]["execution_state"] == "inferred"
    assert "尚未执行系统操作" in events[0]["claim"]
    assert events[0]["source"] == "aliyun_dashscope"


def test_gateway_accepts_only_structured_multimodal_context() -> None:
    result = _coerce_multimodal_context([
        {"input_type": "image", "summary": "ok"},
        {"type": "audio", "normalized_transcript": "nginx"},
        {"input_type": "video", "summary": "skip"},
        "bad",
    ])

    assert len(result) == 2
    assert result[0]["input_type"] == "image"
    assert result[1]["type"] == "audio"


def test_upload_validation_rejects_unsupported_types() -> None:
    validate_image(UploadedBlob("a.png", "image/png", b"123"))
    validate_audio(UploadedBlob("a.webm", "audio/webm", b"123"))

    try:
        validate_image(UploadedBlob("a.txt", "text/plain", b"123"))
    except Exception as exc:
        assert "仅支持" in str(exc)
    else:
        raise AssertionError("unsupported image type was accepted")


def main() -> None:
    test_voice_normalization_marks_ops_terms_and_write_intent()
    test_multimodal_prompt_warns_agent_to_verify_with_real_tools()
    test_ops_entity_extraction_and_tool_recommendations()
    test_low_confidence_result_requires_user_confirmation()
    test_multimodal_trace_events_are_inferred_not_executed()
    test_gateway_accepts_only_structured_multimodal_context()
    test_upload_validation_rejects_unsupported_types()
    print("multimodal input regression OK")


if __name__ == "__main__":
    main()
