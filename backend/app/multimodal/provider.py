"""Provider abstraction for image and voice recognition.

The first implementation targets Alibaba Cloud Model Studio (DashScope).
Recognition results are treated as user-provided evidence, not as verified
system state. Agent code must still confirm operational state with MCP tools.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings


IMAGE_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
AUDIO_CONTENT_TYPES = {
    "audio/wav",
    "audio/wave",
    "audio/x-wav",
    "audio/mpeg",
    "audio/mp3",
    "audio/mp4",
    "audio/m4a",
    "audio/webm",
}
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_AUDIO_BYTES = 20 * 1024 * 1024
OPS_GLOSSARY = [
    ("恩金叉", "nginx"),
    ("恩金克斯", "nginx"),
    ("系统 CTL", "systemctl"),
    ("系统ctl", "systemctl"),
    ("抓nal CTL", "journalctl"),
    ("日志 CTL", "journalctl"),
    ("瑞迪斯", "redis"),
    ("麦 SQL", "mysql"),
    ("八零端口", "80 端口"),
    ("四四三端口", "443 端口"),
    ("var log messages", "/var/log/messages"),
    ("etc 恩金叉", "/etc/nginx"),
    ("etc nginx", "/etc/nginx"),
]
TOOL_DISPLAY_NAMES = {
    "get_service_status": "服务状态检查",
    "get_service_logs": "服务日志检查",
    "read_config_file": "读取配置文件",
    "check_config_syntax": "检查配置语法",
    "check_file_info": "文件信息检查",
    "check_port": "端口占用检查",
    "get_disk_usage": "磁盘使用检查",
    "get_recent_errors": "最近错误日志",
}


@dataclass
class UploadedBlob:
    filename: str
    content_type: str
    data: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


class MultimodalError(RuntimeError):
    """Raised when recognition cannot be completed."""


def validate_image(blob: UploadedBlob) -> None:
    if blob.content_type not in IMAGE_CONTENT_TYPES:
        raise MultimodalError("仅支持 jpg、png、webp 图片")
    if not blob.data:
        raise MultimodalError("图片内容为空")
    if len(blob.data) > MAX_IMAGE_BYTES:
        raise MultimodalError("图片不能超过 10MB")


def validate_audio(blob: UploadedBlob) -> None:
    if blob.content_type not in AUDIO_CONTENT_TYPES:
        raise MultimodalError("仅支持 wav、mp3、m4a、webm 音频")
    if not blob.data:
        raise MultimodalError("音频内容为空")
    if len(blob.data) > MAX_AUDIO_BYTES:
        raise MultimodalError("音频不能超过 20MB")


class AliyunMultimodalProvider:
    """Alibaba Cloud DashScope-backed multimodal provider."""

    def __init__(self) -> None:
        self.api_key = (
            os.environ.get("DASHSCOPE_API_KEY")
            or os.environ.get("ALIYUN_DASHSCOPE_API_KEY")
            or os.environ.get("ALIYUN_BAILIAN_API_KEY")
            or _dashscope_key_from_settings()
            or ""
        )
        self.base_url = os.environ.get(
            "DASHSCOPE_API_BASE",
            _dashscope_base_from_settings(),
        ).rstrip("/")
        self.vision_model = os.environ.get("OPSGUARD_VISION_MODEL", "qwen-vl-ocr")
        self.asr_model = os.environ.get("OPSGUARD_ASR_MODEL", "qwen3-asr-flash")

    async def analyze_image(self, blob: UploadedBlob) -> dict[str, Any]:
        validate_image(blob)
        if not self.api_key:
            raise MultimodalError("未配置阿里云百炼 API Key，无法进行图片识别")

        image_url = f"data:{blob.content_type};base64,{base64.b64encode(blob.data).decode('ascii')}"
        payload = {
            "model": self.vision_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "你是 OpsGuard 的运维截图识别器。请识别图片中的文字和运维线索，"
                                "只输出 JSON，不要输出 Markdown。字段必须包含 input_type、image_category、"
                                "summary、extracted_text、entities、diagnosis_hints、recommended_tools、"
                                "confidence、warnings。注意：不要声称已经真实执行任何系统操作。"
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ],
            "temperature": 0,
        }
        data = await self._post_json("/chat/completions", payload, "图片识别服务暂不可用，请改用文字描述或稍后重试。")
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        result = _parse_json_content(content)
        _normalize_ocr_text_fields(result)
        if not result.get("input_type"):
            result["input_type"] = "image"
        if not result.get("image_category"):
            result["image_category"] = "unknown"
        if not result.get("summary"):
            result["summary"] = _compact(content, 300) or "图片识别完成"
        if not result.get("extracted_text"):
            cloud_ocr_text = await self._recognize_image_text(blob)
            if cloud_ocr_text:
                result["extracted_text"] = cloud_ocr_text
                result["summary"] = f"图片中识别到文本：{_compact(cloud_ocr_text, 200)}"
                result["fallbacks"] = _merge_list(result.get("fallbacks"), ["cloud_ocr_retry"])
        if not result.get("extracted_text"):
            ocr_text = _local_ocr_image(blob)
            if ocr_text:
                result["extracted_text"] = ocr_text
                result["summary"] = f"图片中识别到文本：{_compact(ocr_text, 200)}"
                result["warnings"] = _merge_list(
                    result.get("warnings"),
                    ["视觉模型未返回有效文本，已使用本地 OCR 兜底。"],
                )
                result["fallbacks"] = _merge_list(result.get("fallbacks"), ["local_tesseract_ocr"])
        result.setdefault("extracted_text", "")
        result.setdefault("entities", {})
        result.setdefault("diagnosis_hints", [])
        result.setdefault("recommended_tools", [])
        result.setdefault("confidence", "medium")
        result.setdefault("warnings", [])
        result["provider"] = "aliyun_dashscope"
        result["model"] = self.vision_model
        result["file"] = _file_summary(blob)
        return _normalize_image_result(result)

    async def transcribe_audio(self, blob: UploadedBlob) -> dict[str, Any]:
        validate_audio(blob)
        if not self.api_key:
            raise MultimodalError("未配置阿里云百炼 API Key，无法进行语音识别")

        audio_url = f"data:{blob.content_type};base64,{base64.b64encode(blob.data).decode('ascii')}"
        payload = {
            "model": self.asr_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": audio_url,
                                "format": _audio_format(blob),
                            },
                        }
                    ],
                }
            ],
            "stream": False,
            "asr_options": {
                "language": "zh",
                "enable_itn": False,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            raise MultimodalError(f"语音识别服务暂不可用，请改用文字输入或稍后重试。详情: {exc}") from exc

        transcript = _extract_asr_text(payload)
        if not transcript:
            raise MultimodalError("语音识别未返回有效文本")
        normalized = normalize_transcript(transcript)
        normalized.update({
            "input_type": "audio",
            "provider": "aliyun_dashscope",
            "model": self.asr_model,
            "file": _file_summary(blob),
        })
        return normalized

    async def _recognize_image_text(self, blob: UploadedBlob) -> str:
        image_url = f"data:{blob.content_type};base64,{base64.b64encode(blob.data).decode('ascii')}"
        payload = {
            "model": self.vision_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "请逐字识别图片中的文字，只输出识别到的文本。"},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ],
            "temperature": 0,
        }
        try:
            data = await self._post_json(
                "/chat/completions",
                payload,
                "图片 OCR 识别服务暂不可用，请改用文字描述或稍后重试。",
            )
        except MultimodalError:
            return ""
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        parsed = _parse_json_content(content)
        _normalize_ocr_text_fields(parsed)
        return _compact(parsed.get("extracted_text") or content, 2000)

    async def _post_json(self, path: str, payload: dict[str, Any], user_error: str) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(f"{self.base_url}{path}", headers=headers, json=payload)
                response.raise_for_status()
                return response.json()
        except Exception as exc:
            raise MultimodalError(f"{user_error} 详情: {exc}") from exc


def normalize_transcript(text: str) -> dict[str, Any]:
    """Apply a small operations glossary over ASR text."""
    normalized = text.strip()
    corrections: list[dict[str, str]] = []
    for source, target in OPS_GLOSSARY:
        if source in normalized:
            normalized = normalized.replace(source, target)
            corrections.append({"from": source, "to": target, "reason": "运维词表匹配"})
    enhanced = enhance_ops_semantics({
        "input_type": "audio",
        "raw_transcript": text,
        "normalized_transcript": normalized,
        "extracted_text": normalized,
    })

    return {
        "raw_transcript": text,
        "normalized_transcript": normalized,
        "corrections": corrections,
        "entities": enhanced["entities"],
        "recommended_tools": enhanced["recommended_tools"],
        "diagnosis_hints": enhanced["diagnosis_hints"],
        "confidence": "medium" if corrections else enhanced["confidence"],
        "needs_user_confirmation": enhanced["needs_user_confirmation"],
        "requires_write_confirmation": _contains_write_intent(normalized),
    }


def build_multimodal_prompt_context(items: list[dict[str, Any]]) -> str:
    """Format recognition results as a compact Agent context block."""
    if not items:
        return ""
    lines = [
        "",
        "## 多模态输入识别结果",
        "以下内容来自用户上传的图片或语音识别，可能存在误识别。涉及系统状态、配置、日志或执行结果时，必须通过真实 MCP 工具确认；不得仅凭识别结果声称已执行操作。",
    ]
    for idx, item in enumerate(items, 1):
        input_type = item.get("input_type") or item.get("type") or "unknown"
        label = "图片" if input_type == "image" else "语音" if input_type == "audio" else "附件"
        lines.append(f"{idx}. 来源：{label}识别")
        if item.get("summary"):
            lines.append(f"   摘要：{item.get('summary')}")
        if item.get("normalized_transcript"):
            lines.append(f"   识别文本：{item.get('normalized_transcript')}")
        elif item.get("extracted_text"):
            lines.append(f"   识别文本：{_compact(item.get('extracted_text'), 500)}")
        entities = item.get("entities") or {}
        entity_text = _format_entities(entities)
        if entity_text:
            lines.append(f"   识别实体：{entity_text}")
        hints = item.get("diagnosis_hints") or []
        if hints:
            lines.append(f"   诊断提示：{'；'.join(map(str, hints[:5]))}")
        tools = item.get("recommended_tools") or []
        if tools:
            lines.append(f"   建议只读检查：{_format_tools(tools)}")
        if item.get("confidence"):
            lines.append(f"   置信度：{item.get('confidence')}")
    return "\n".join(lines)


def trace_events_from_context(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build trace evidence events for already-recognized multimodal context."""
    from app.agent.trace_evidence import build_evidence, trace_event

    events = []
    for item in items or []:
        input_type = item.get("input_type") or item.get("type") or "unknown"
        phase = "image_recognition" if input_type == "image" else "voice_recognition" if input_type == "audio" else "multimodal_recognition"
        source = item.get("provider") or "multimodal_input"
        if input_type == "audio":
            content = (
                "语音识别完成\n"
                f"原始文本：{item.get('raw_transcript', '')}\n"
                f"纠错后：{item.get('normalized_transcript', '')}"
            )
            observed = {
                "raw_transcript": item.get("raw_transcript"),
                "normalized_transcript": item.get("normalized_transcript"),
                "corrections": item.get("corrections") or [],
            }
            claim = "语音输入已转换为可编辑文本，尚未执行系统操作"
        else:
            content = (
                "图片识别完成\n"
                f"摘要：{item.get('summary', '')}\n"
                f"置信度：{item.get('confidence', 'medium')}"
            )
            observed = {
                "summary": item.get("summary"),
                "entities": item.get("entities") or {},
                "diagnosis_hints": item.get("diagnosis_hints") or [],
            }
            claim = "图片内容已识别为用户提供证据，尚未执行系统操作"
        events.append(trace_event(
            phase=phase,
            event_type="success",
            content=content,
            evidence=build_evidence(
                claim=claim,
                evidence_type="user input",
                source=source,
                observed=observed,
                confidence=item.get("confidence") or "medium",
                execution_state="inferred",
                next_check="请使用真实 MCP 工具确认图片或语音中提到的系统状态。",
            ),
            metadata={"multimodal": item},
        ))
    return events


def _parse_json_content(content: str) -> dict[str, Any]:
    text = str(content or "").strip()
    if not text:
        return {}
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            try:
                parsed = json.loads(match.group(0))
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                pass
    return {"summary": _compact(text, 300), "extracted_text": text}


def _normalize_ocr_text_fields(result: dict[str, Any]) -> None:
    """Map common OCR response keys from cloud models into extracted_text."""
    if result.get("extracted_text"):
        return
    for key in ("text", "ocr_text", "recognized_text", "content", "transcription"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            result["extracted_text"] = value.strip()
            return
    lines = result.get("lines")
    if isinstance(lines, list):
        joined = "\n".join(str(item).strip() for item in lines if str(item).strip())
        if joined:
            result["extracted_text"] = joined


def _local_ocr_image(blob: UploadedBlob) -> str:
    """Best-effort OCR fallback for clear screenshots when the VLM returns empty text."""
    suffix = ".png"
    if blob.content_type in {"image/jpeg", "image/jpg"}:
        suffix = ".jpg"
    elif blob.content_type == "image/webp":
        suffix = ".webp"
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
            tmp.write(blob.data)
            tmp.flush()
            result = subprocess.run(
                ["tesseract", tmp.name, "stdout", "-l", "eng", "--psm", "6"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        if result.returncode != 0:
            return ""
        return _compact(result.stdout, 2000)
    except Exception:
        return ""


def _extract_asr_text(payload: dict[str, Any]) -> str:
    direct = payload.get("text") or payload.get("transcript")
    if direct:
        return str(direct).strip()

    message = (
        payload.get("choices", [{}])[0]
        .get("message", {})
    )
    content = message.get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text") or item.get("transcript")
            if text:
                parts.append(str(text))
        return " ".join(parts).strip()
    return ""


def _audio_format(blob: UploadedBlob) -> str:
    content_type = (blob.content_type or "").lower()
    filename = (blob.filename or "").lower()
    if "webm" in content_type or filename.endswith(".webm"):
        return "webm"
    if "mpeg" in content_type or "mp3" in content_type or filename.endswith(".mp3"):
        return "mp3"
    if "mp4" in content_type or filename.endswith(".mp4"):
        return "mp4"
    if "m4a" in content_type or filename.endswith(".m4a"):
        return "m4a"
    return "wav"


def _normalize_image_result(result: dict[str, Any]) -> dict[str, Any]:
    model_input_type = result.get("input_type")
    if isinstance(model_input_type, str) and model_input_type not in {"image", ""}:
        if not result.get("image_category") or result.get("image_category") == "unknown":
            result["image_category"] = model_input_type
    result["input_type"] = "image"
    result["extracted_text"] = _normalize_text_value(result.get("extracted_text"))
    entities = result.get("entities")
    result["entities"] = entities if isinstance(entities, dict) else {}
    for key in ("diagnosis_hints", "recommended_tools", "warnings"):
        value = result.get(key)
        if not isinstance(value, list):
            result[key] = [] if value in (None, "") else [value]
    if result.get("confidence") not in {"high", "medium", "low"}:
        result["confidence"] = "medium"
    return enhance_ops_semantics(result)


def _normalize_text_value(value: Any) -> str:
    """Coerce OCR text fields into prompt-ready plain text."""
    if isinstance(value, list):
        return "\n".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def enhance_ops_semantics(result: dict[str, Any]) -> dict[str, Any]:
    """Validate schema, extract operations entities, and recommend safe checks."""
    text = _semantic_text(result)
    entities = _merge_entities(result.get("entities"), extract_ops_entities(text))
    result["entities"] = entities
    result["diagnosis_hints"] = _merge_list(result.get("diagnosis_hints"), _diagnosis_hints(text, entities))
    result["recommended_tools"] = _merge_recommended_tools(result.get("recommended_tools"), _recommend_tools(text, entities))
    result["warnings"] = _merge_list(result.get("warnings"), [])
    if result.get("confidence") not in {"high", "medium", "low"}:
        result["confidence"] = "medium"
    result["needs_user_confirmation"] = result["confidence"] == "low"
    if result["needs_user_confirmation"] and not any("识别置信度较低" in str(item) for item in result["warnings"]):
        result["warnings"].append("识别置信度较低，请核对后再发送。")
    return result


def extract_ops_entities(text: str) -> dict[str, list[Any]]:
    """Extract common SRE/Ops entities from recognized text."""
    text = text or ""
    services = _unique(re.findall(r"\b(nginx|redis|mysql|mariadb|postgresql|postgres|apache2?|httpd|docker|containerd|kubelet|etcd|prometheus|grafana)\b", text, re.I))
    service_units = _unique(re.findall(r"\b([a-zA-Z0-9_.@-]+)\.service\b", text))
    services = _unique([_strip_service_suffix(item) for item in services + service_units])
    paths = _unique(re.findall(r"(?<![\w.-])/(?:[\w.@:+-]+/)*[\w.@:+-]+", text))
    ports = sorted({int(port) for port in re.findall(r"(?:(?:端口|port)\s*[:：]?\s*|\b:)(\d{1,5})\b", text, re.I) if 0 < int(port) <= 65535})
    commands = _unique(re.findall(r"\b(rm\s+-\S+(?:\s+\S+)?|systemctl\s+\S+(?:\s+\S+)?|journalctl\s+[^\n\r;]+|nginx\s+-t|ss\s+-\S+|netstat\s+-\S+|df\s+-\S+|ps\s+[^\n\r;]+)", text, re.I))
    error_keywords = _unique([
        item.group(0)
        for item in re.finditer(
            r"(permission denied|failed|error|exception|timeout|refused|no such file|address already in use|syntax error|磁盘|满|失败|拒绝|超时|不存在|权限|语法错误|端口占用)",
            text,
            re.I,
        )
    ])
    error_codes = _unique([
        _normalize_error_code(item.group(0))
        for item in re.finditer(
            r"\b(?:HTTP\s*)?(?:4\d{2}|5\d{2})\b|\b(?:ERR|ERROR|E)[_-]?[A-Z0-9]{2,}\b",
            text,
            re.I,
        )
    ])
    timestamps = _unique(re.findall(r"\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?\b|\b\d{2}:\d{2}:\d{2}\b", text))
    return {
        "services": services,
        "ports": ports,
        "paths": paths,
        "commands": commands,
        "error_keywords": error_keywords,
        "error_codes": error_codes,
        "timestamps": timestamps,
    }


def _file_summary(blob: UploadedBlob) -> dict[str, Any]:
    return {
        "filename": blob.filename,
        "content_type": blob.content_type,
        "size": len(blob.data),
        "sha256": blob.sha256,
    }


def _compact(value: Any, max_chars: int = 500) -> str:
    text = str(value or "")
    text = " ".join(text.split())
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


def _format_entities(entities: dict[str, Any]) -> str:
    parts = []
    for key in ("services", "ports", "paths", "commands", "error_keywords", "error_codes", "timestamps"):
        value = entities.get(key)
        if isinstance(value, list) and value:
            parts.append(f"{key}={', '.join(map(str, value[:5]))}")
    return "；".join(parts)


def _format_tools(tools: list[Any]) -> str:
    formatted = []
    for tool in tools[:5]:
        if isinstance(tool, dict):
            tool_id = str(tool.get("tool") or tool.get("name") or "")
            display_name = str(tool.get("display_name") or _tool_display_name(tool_id))
            formatted.append(f"{display_name}({tool_id})" if tool_id else display_name)
        else:
            formatted.append(str(tool))
    return "，".join(formatted)


def _semantic_text(result: dict[str, Any]) -> str:
    parts = [
        result.get("summary"),
        result.get("extracted_text"),
        result.get("raw_transcript"),
        result.get("normalized_transcript"),
    ]
    return "\n".join(str(item) for item in parts if item)


def _merge_entities(existing: Any, extracted: dict[str, list[Any]]) -> dict[str, list[Any]]:
    merged = existing if isinstance(existing, dict) else {}
    result: dict[str, list[Any]] = {}
    for key in ("services", "ports", "paths", "commands", "error_keywords", "error_codes", "timestamps"):
        values = []
        current = merged.get(key)
        if isinstance(current, list):
            values.extend(current)
        elif current not in (None, ""):
            values.append(current)
        values.extend(extracted.get(key) or [])
        result[key] = _unique(values)
    return result


def _merge_list(existing: Any, additions: list[Any]) -> list[Any]:
    values = existing if isinstance(existing, list) else ([] if existing in (None, "") else [existing])
    return _unique([*values, *additions])


def _merge_recommended_tools(existing: Any, additions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    values = existing if isinstance(existing, list) else []
    merged: list[dict[str, Any]] = []
    seen = set()
    for item in [*values, *additions]:
        if not isinstance(item, dict):
            continue
        tool = item.get("tool") or item.get("name")
        key = json.dumps({"tool": tool, "args": item.get("args") or {}}, sort_keys=True, ensure_ascii=False)
        if tool and key not in seen:
            seen.add(key)
            tool_id = str(tool)
            merged.append({
                "tool": tool_id,
                "display_name": item.get("display_name") or _tool_display_name(tool_id),
                "args": item.get("args") or {},
                "reason": item.get("reason") or "多模态识别推荐的只读检查",
                "readonly": True,
            })
    return merged


def _diagnosis_hints(text: str, entities: dict[str, list[Any]]) -> list[str]:
    hints: list[str] = []
    lowered = text.lower()
    if entities.get("services") and any(token in lowered for token in ("failed", "active:", "inactive", "dead", "启动失败", "失败")):
        hints.append("疑似服务状态异常，建议先读取服务状态和最近日志。")
    if entities.get("ports") or any(token in lowered for token in ("address already in use", "端口占用", "listen")):
        hints.append("疑似端口或监听状态相关问题，建议检查端口占用。")
    if any(token in lowered for token in ("syntax error", "nginx -t", "语法错误")):
        hints.append("疑似配置语法问题，建议检查配置语法并读取相关配置文件。")
    if any(token in lowered for token in ("no space", "disk", "磁盘", "满")):
        hints.append("疑似磁盘空间问题，建议检查磁盘使用率和大文件。")
    if any(token in lowered for token in ("permission denied", "权限", "拒绝")):
        hints.append("疑似权限问题，建议检查文件信息和服务日志。")
    return hints


def _recommend_tools(text: str, entities: dict[str, list[Any]]) -> list[dict[str, Any]]:
    lowered = text.lower()
    tools: list[dict[str, Any]] = []
    for service in entities.get("services") or []:
        tools.append({"tool": "get_service_status", "args": {"service": service}, "reason": f"识别到服务 {service}"})
        tools.append({"tool": "get_service_logs", "args": {"service": service, "lines": 80}, "reason": f"识别到服务 {service} 相关日志/状态线索"})
    for path in entities.get("paths") or []:
        if _looks_like_config_path(str(path)):
            tools.append({"tool": "read_config_file", "args": {"filepath": path}, "reason": f"识别到配置路径 {path}"})
            tools.append({"tool": "check_config_syntax", "args": {"filepath": path}, "reason": f"识别到配置路径 {path}"})
        else:
            tools.append({"tool": "check_file_info", "args": {"filepath": path}, "reason": f"识别到文件路径 {path}"})
    for port in entities.get("ports") or []:
        tools.append({"tool": "check_port", "args": {"port": port}, "reason": f"识别到端口 {port}"})
    if any(token in lowered for token in ("disk", "磁盘", "no space", "满")):
        tools.append({"tool": "get_disk_usage", "args": {"path": "/"}, "reason": "识别到磁盘空间相关线索"})
    if entities.get("error_codes") or any(token in lowered for token in ("error", "exception", "错误", "异常")):
        tools.append({"tool": "get_recent_errors", "args": {"lines": 80}, "reason": "识别到错误日志线索"})
    return tools


def _tool_display_name(tool_id: str) -> str:
    return TOOL_DISPLAY_NAMES.get(tool_id, tool_id or "推荐检查")


def _normalize_error_code(value: str) -> str:
    match = re.search(r"(4\d{2}|5\d{2})", value)
    return match.group(1) if match else value


def _looks_like_config_path(path: str) -> bool:
    return any(token in path for token in ("/etc/", ".conf", ".ini", ".yaml", ".yml", ".json", "nginx"))


def _strip_service_suffix(value: Any) -> str:
    text = str(value)
    return text[:-8] if text.endswith(".service") else text


def _unique(values: list[Any]) -> list[Any]:
    result = []
    seen = set()
    for value in values:
        if value in (None, ""):
            continue
        key = str(value).lower()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _contains_write_intent(text: str) -> bool:
    return bool(re.search(r"(重启|启动|停止|关闭|删除|清理|修改|写入|保存|应用|创建|新建|安装|卸载|开放|禁用|启用|\brm\s+-|\bsystemctl\s+(?:restart|start|stop|disable|enable)\b)", text, re.I))


def _dashscope_key_from_settings() -> str:
    candidates = [settings.llm.primary]
    if settings.llm.fallback:
        candidates.append(settings.llm.fallback)
    for item in candidates:
        if item.api_key and "dashscope" in (item.api_base or "").lower():
            return item.api_key
    return ""


def _dashscope_base_from_settings() -> str:
    candidates = [settings.llm.primary]
    if settings.llm.fallback:
        candidates.append(settings.llm.fallback)
    for item in candidates:
        if item.api_base and "dashscope" in item.api_base.lower():
            return item.api_base
    return "https://dashscope.aliyuncs.com/compatible-mode/v1"


provider = AliyunMultimodalProvider()
