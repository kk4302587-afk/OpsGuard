"""Download and prepare the prompt injection classifier model.

Downloads a pre-trained DeBERTa-based prompt injection detection model
and exports it to ONNX format for lightweight inference.

Usage:
    python scripts/download_model.py

Requirements:
    pip install transformers torch onnx onnxruntime

Note: This script only needs to run ONCE during setup.
      After export, only onnxruntime + tokenizers are needed at runtime.
"""

import os
import sys
from pathlib import Path

def main():
    print("=" * 50)
    print("  OpsGuard - 下载 Prompt Injection 分类模型")
    print("=" * 50)

    model_dir = Path(__file__).parent.parent / "backend" / "models" / "prompt_guard"
    model_dir.mkdir(parents=True, exist_ok=True)

    # Check if already downloaded
    onnx_path = model_dir / "model.onnx"
    if onnx_path.exists():
        print(f"[INFO] 模型已存在: {onnx_path}")
        print("[INFO] 如需重新下载，请删除 backend/models/prompt_guard/ 目录")
        return

    try:
        import torch
        from transformers import AutoTokenizer, AutoModelForSequenceClassification
    except ImportError:
        print("[ERROR] 需要安装 transformers 和 torch:")
        print("  pip install transformers torch")
        print("")
        print("注意: 这些只在模型导出时需要，运行时只需 onnxruntime + tokenizers")
        sys.exit(1)

    # Use a lightweight prompt injection detection model
    # Option 1: protectai/deberta-v3-base-prompt-injection-v2 (recommended)
    # Option 2: meta-llama/Prompt-Guard-86M (requires access)
    model_name = "protectai/deberta-v3-base-prompt-injection-v2"

    print(f"[INFO] 下载模型: {model_name}")
    print("[INFO] 首次下载可能需要几分钟...")

    try:
        tokenizer = AutoTokenizer.from_pretrained(model_name)
        model = AutoModelForSequenceClassification.from_pretrained(model_name)
        model.eval()
    except Exception as e:
        print(f"[ERROR] 下载失败: {e}")
        print("")
        print("如果网络问题，可以手动下载模型到 backend/models/prompt_guard/")
        sys.exit(1)

    # Save tokenizer
    print("[INFO] 保存 tokenizer...")
    tokenizer.save_pretrained(str(model_dir))

    # Export to ONNX
    print("[INFO] 导出 ONNX 模型...")
    dummy_input = tokenizer(
        "Hello, how are you?",
        return_tensors="pt",
        padding="max_length",
        truncation=True,
        max_length=512,
    )

    torch.onnx.export(
        model,
        (dummy_input["input_ids"], dummy_input["attention_mask"]),
        str(onnx_path),
        input_names=["input_ids", "attention_mask"],
        output_names=["logits"],
        dynamic_axes={
            "input_ids": {0: "batch", 1: "sequence"},
            "attention_mask": {0: "batch", 1: "sequence"},
        },
        opset_version=14,
    )

    # Verify
    import onnxruntime as ort
    session = ort.InferenceSession(str(onnx_path))
    print(f"[INFO] ONNX 模型验证成功")

    # Save label mapping
    label_map_path = model_dir / "label_map.json"
    import json
    label_map = {"0": "safe", "1": "injection"}
    with open(label_map_path, "w") as f:
        json.dump(label_map, f)

    print("")
    print("=" * 50)
    print(f"[SUCCESS] 模型已保存到: {model_dir}")
    print(f"  - model.onnx ({onnx_path.stat().st_size / 1024 / 1024:.1f} MB)")
    print(f"  - tokenizer files")
    print(f"  - label_map.json")
    print("")
    print("运行时只需: onnxruntime + tokenizers (无需 torch)")
    print("=" * 50)


if __name__ == "__main__":
    main()
