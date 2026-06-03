"""Regression checks for multimodal input handling."""

import os
from pathlib import Path

os.chdir(Path(__file__).parent)

from app.multimodal.provider import (
    AliyunMultimodalProvider,
    UploadedBlob,
    build_multimodal_prompt_context,
    enhance_ops_semantics,
    extract_ops_entities,
    normalize_transcript,
    trace_events_from_context,
    validate_audio,
    validate_image,
)
from app.websocket.gateway import _coerce_multimodal_context, _hydrate_multimodal_context


class _MiniMonkeyPatch:
    def setattr(self, target, name, value, **kwargs) -> None:
        setattr(target, name, value)


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


def test_gateway_hydrates_multimodal_context_from_attachment_id(tmp_path=None, monkeypatch=None) -> None:
    import aiosqlite
    import asyncio
    import json
    import tempfile

    if tmp_path is None:
        tmp_path = Path(tempfile.mkdtemp(prefix="opsguard-mm-test-"))
    if monkeypatch is None:
        monkeypatch = _MiniMonkeyPatch()
    db_path = Path(tmp_path) / "knowledge.db"

    async def scenario() -> None:
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                """CREATE TABLE message_attachments (
                id TEXT PRIMARY KEY,
                input_type TEXT NOT NULL,
                filename TEXT NOT NULL,
                recognition_json TEXT
                )"""
            )
            await db.execute(
                """INSERT INTO message_attachments
                (id, input_type, filename, recognition_json)
                VALUES (?, ?, ?, ?)""",
                (
                    "att-image-1",
                    "image",
                    "nginx.png",
                    json.dumps({
                        "input_type": "image",
                        "summary": "截图显示 nginx failed",
                        "confidence": "medium",
                    }, ensure_ascii=False),
                ),
            )
            await db.commit()

        from app import database

        monkeypatch.setattr(database, "get_knowledge_db_path", lambda: str(db_path))
        hydrated = await _hydrate_multimodal_context(
            [],
            [{"id": "att-image-1", "type": "image"}],
        )

        assert len(hydrated) == 1
        assert hydrated[0]["input_type"] == "image"
        assert hydrated[0]["attachment_id"] == "att-image-1"
        assert "nginx failed" in hydrated[0]["summary"]

    asyncio.run(scenario())


def test_image_analysis_falls_back_to_local_ocr_when_vlm_text_is_empty(monkeypatch) -> None:
    import asyncio

    image_path = Path("data/attachments/591edd4c-0206-4d4d-80fd-50038593b3fb.png")
    if not image_path.exists():
        return

    async def fake_post_json(*args, **kwargs):
        return {"choices": [{"message": {"content": '{"summary":"","extracted_text":"","confidence":"medium"}'}}]}

    async def fake_cloud_ocr(blob):
        return ""

    provider = AliyunMultimodalProvider()
    provider.api_key = "test-key"
    original_post_json = provider._post_json
    original_cloud_ocr = provider._recognize_image_text
    monkeypatch.setattr(provider, "_post_json", fake_post_json)
    monkeypatch.setattr(provider, "_recognize_image_text", fake_cloud_ocr)

    try:
        result = asyncio.run(provider.analyze_image(UploadedBlob("danger.png", "image/png", image_path.read_bytes())))
    finally:
        provider._post_json = original_post_json
        provider._recognize_image_text = original_cloud_ocr

    assert "rm -rf /tmp/test" in result["extracted_text"]
    assert "systemctl restart nginx" in result["extracted_text"]
    assert "local_tesseract_ocr" in result["fallbacks"]
    assert "nginx" in result["entities"]["services"]
    assert "systemctl restart nginx" in result["entities"]["commands"]


def test_image_analysis_accepts_cloud_ocr_text_field(monkeypatch) -> None:
    import asyncio

    async def fake_post_json(*args, **kwargs):
        return {"choices": [{"message": {"content": '{"text":"rm -rf /tmp/test\\nsystemctl restart nginx"}'}}]}

    provider = AliyunMultimodalProvider()
    provider.api_key = "test-key"
    original_post_json = provider._post_json
    monkeypatch.setattr(provider, "_post_json", fake_post_json)

    try:
        result = asyncio.run(provider.analyze_image(UploadedBlob("danger.png", "image/png", b"fake-image")))
    finally:
        provider._post_json = original_post_json

    assert result["extracted_text"] == "rm -rf /tmp/test\nsystemctl restart nginx"
    assert "fallbacks" not in result
    assert "nginx" in result["entities"]["services"]
    assert "systemctl restart nginx" in result["entities"]["commands"]


def test_image_analysis_retries_cloud_ocr_before_local_fallback(monkeypatch) -> None:
    import asyncio

    calls = []

    async def fake_post_json(*args, **kwargs):
        calls.append(args)
        if len(calls) == 2:
            return {"choices": [{"message": {"content": "rm -rf /tmp/test\nsystemctl restart nginx"}}]}
        return {"choices": [{"message": {"content": '{"summary":"","extracted_text":"","confidence":"medium"}'}}]}

    provider = AliyunMultimodalProvider()
    provider.api_key = "test-key"
    monkeypatch.setattr(provider, "_post_json", fake_post_json)

    result = asyncio.run(provider.analyze_image(UploadedBlob("danger.png", "image/png", b"fake-image")))

    assert len(calls) == 2
    assert calls[1][1]["messages"][0]["content"][0]["text"].startswith("请逐字识别")
    assert result["fallbacks"] == ["cloud_ocr_retry"]


def test_upload_validation_rejects_unsupported_types() -> None:
    validate_image(UploadedBlob("a.png", "image/png", b"123"))
    validate_audio(UploadedBlob("a.webm", "audio/webm", b"123"))

    try:
        validate_image(UploadedBlob("a.txt", "text/plain", b"123"))
    except Exception as exc:
        assert "仅支持" in str(exc)
    else:
        raise AssertionError("unsupported image type was accepted")


def test_audio_transcription_uses_dashscope_chat_completions(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {"message": {"content": "帮我检查 nginx 服务"}}
                ]
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

    import app.multimodal.provider as provider_module

    monkeypatch.setattr(provider_module.httpx, "AsyncClient", FakeClient)
    provider = AliyunMultimodalProvider()
    provider.api_key = "test-key"
    provider.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    import asyncio

    result = asyncio.run(provider.transcribe_audio(UploadedBlob("voice.webm", "audio/webm", b"abc")))

    assert result["normalized_transcript"] == "帮我检查 nginx 服务"
    assert calls
    assert calls[0][0].endswith("/chat/completions")
    assert not calls[0][0].endswith("/audio/transcriptions")
    assert calls[0][1]["json"]["messages"][0]["content"][0]["type"] == "input_audio"


def main() -> None:
    test_voice_normalization_marks_ops_terms_and_write_intent()
    test_multimodal_prompt_warns_agent_to_verify_with_real_tools()
    test_ops_entity_extraction_and_tool_recommendations()
    test_low_confidence_result_requires_user_confirmation()
    test_multimodal_trace_events_are_inferred_not_executed()
    test_gateway_accepts_only_structured_multimodal_context()
    test_gateway_hydrates_multimodal_context_from_attachment_id()
    test_image_analysis_falls_back_to_local_ocr_when_vlm_text_is_empty(_MiniMonkeyPatch())
    test_image_analysis_accepts_cloud_ocr_text_field(_MiniMonkeyPatch())
    test_image_analysis_retries_cloud_ocr_before_local_fallback(_MiniMonkeyPatch())
    test_upload_validation_rejects_unsupported_types()
    test_audio_transcription_uses_dashscope_chat_completions(_MiniMonkeyPatch())
    print("multimodal input regression OK")


if __name__ == "__main__":
    main()
