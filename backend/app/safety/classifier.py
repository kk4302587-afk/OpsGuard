"""Layer 2: BERT-based prompt injection classifier.

Semantic-level detection using a lightweight DeBERTa model exported to ONNX.
Independent from the main LLM, cannot be bypassed via conversation.
Auto-degrades gracefully if model is unavailable.

Model: protectai/deberta-v3-base-prompt-injection-v2 (ONNX export)
Runtime: onnxruntime (no PyTorch needed)
Latency: ~20-50ms per inference on CPU
"""

import json
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from app.config import settings


@dataclass
class ClassifierResult:
    """Result of classifier inference."""
    is_safe: bool
    confidence: float
    label: str  # "safe" or "injection"


class PromptClassifier:
    """BERT-based prompt injection classifier with graceful degradation.

    Uses ONNX Runtime for inference — no PyTorch dependency at runtime.
    Model must be prepared using scripts/download_model.py first.
    """

    def __init__(self):
        self._session = None  # ONNX InferenceSession
        self._tokenizer = None
        self._np = None
        self._label_map: dict[int, str] = {0: "safe", 1: "injection"}
        self._available = False
        self._max_length = 512
        self._load_model()

    def _load_model(self):
        """Attempt to load the ONNX classifier model."""
        if not settings.safety.classifier.enabled:
            logger.info("Prompt classifier disabled in config")
            return

        model_path = Path(settings.safety.classifier.model_path)
        onnx_path = model_path / "model.onnx"
        label_map_path = model_path / "label_map.json"

        if not onnx_path.exists():
            logger.warning(
                f"Classifier model not found at {onnx_path}. "
                "Running in degraded mode (Layer 2 disabled). "
                "Run 'python scripts/download_model.py' to download the model."
            )
            return

        try:
            import numpy as np
            import onnxruntime as ort

            # Load ONNX model
            sess_options = ort.SessionOptions()
            sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            sess_options.intra_op_num_threads = 2  # Limit CPU threads

            self._session = ort.InferenceSession(
                str(onnx_path),
                sess_options,
                providers=['CPUExecutionProvider'],
            )

            # Load tokenizer
            from tokenizers import Tokenizer
            tokenizer_path = model_path / "tokenizer.json"
            if tokenizer_path.exists():
                self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
                self._tokenizer.enable_truncation(max_length=self._max_length)
                self._tokenizer.enable_padding(length=self._max_length)
            else:
                # Try loading with transformers-style files
                self._tokenizer = self._load_hf_tokenizer(model_path)
                if not self._tokenizer:
                    logger.warning("Tokenizer not found. Classifier disabled.")
                    return

            # Load label map
            if label_map_path.exists():
                with open(label_map_path, "r") as f:
                    raw_map = json.load(f)
                    self._label_map = {int(k): v for k, v in raw_map.items()}

            self._np = np
            self._available = True
            logger.info(f"Prompt classifier loaded successfully from {onnx_path}")

        except ImportError as e:
            logger.warning(f"numpy, onnxruntime, or tokenizers not installed: {e}. Classifier disabled.")
            if not settings.safety.classifier.fallback_on_error:
                raise
        except Exception as e:
            logger.warning(f"Failed to load classifier model: {e}. Running in degraded mode.")
            if not settings.safety.classifier.fallback_on_error:
                raise

    def _load_hf_tokenizer(self, model_path: Path):
        """Try to load a HuggingFace-style tokenizer."""
        try:
            from tokenizers import Tokenizer

            # Check for tokenizer.json (fast tokenizer)
            tokenizer_json = model_path / "tokenizer.json"
            if tokenizer_json.exists():
                tok = Tokenizer.from_file(str(tokenizer_json))
                tok.enable_truncation(max_length=self._max_length)
                tok.enable_padding(length=self._max_length)
                return tok
        except Exception:
            pass
        return None

    @property
    def is_available(self) -> bool:
        """Whether the classifier is loaded and ready."""
        return self._available

    def classify(self, text: str) -> ClassifierResult:
        """Classify input text as safe or injection attempt.

        Args:
            text: User input to classify

        Returns:
            ClassifierResult with is_safe, confidence, and label
        """
        if not self._available:
            return ClassifierResult(is_safe=True, confidence=0.0, label="safe")

        # Short text exemption: the BERT classifier is trained mostly on English
        # injection patterns and is unreliable on short conversational replies like
        # "执行", "确认", "yes", "do it", "那就清理 /tmp 下的大文件吧". These are common
        # follow-ups after the Agent asks for approval, not injection attempts.
        # Rule engine + LLM system prompt still defend against real attacks
        # (real injection templates like "ignore previous instructions and ..."
        # are typically much longer and caught by the rule engine first anyway).
        stripped = text.strip()
        if len(stripped) < 40:
            return ClassifierResult(is_safe=True, confidence=0.0, label="safe_short")

        # CJK exemption: the upstream model (protectai/deberta-v3-base-prompt-injection-v2)
        # is trained almost exclusively on English. It produces ~99% "injection" scores
        # on benign Chinese sentences such as "清理 /tmp 下的垃圾文件吧" or
        # "那就清理 /tmp 下的大文件吧". Chinese attacks are already covered by the rule
        # engine (config.yaml has 16+ Chinese injection patterns) and the LLM system
        # prompt (Layer 3). So when the input is predominantly non-ASCII, skip Layer 2
        # entirely instead of relying on an unreliable classifier.
        ascii_chars = sum(1 for c in stripped if ord(c) < 128)
        if ascii_chars / len(stripped) < 0.5:
            return ClassifierResult(is_safe=True, confidence=0.0, label="safe_cjk")

        try:
            np = self._np
            if np is None:
                return ClassifierResult(is_safe=True, confidence=0.0, label="safe")

            # Tokenize
            encoding = self._tokenizer.encode(text)
            input_ids = np.array([encoding.ids], dtype=np.int64)
            attention_mask = np.array([encoding.attention_mask], dtype=np.int64)

            # Run inference
            outputs = self._session.run(
                None,
                {"input_ids": input_ids, "attention_mask": attention_mask},
            )

            logits = outputs[0][0]  # Shape: [num_classes]

            # Softmax
            exp_logits = np.exp(logits - np.max(logits))
            probs = exp_logits / exp_logits.sum()

            # Get prediction
            predicted_class = int(np.argmax(probs))
            confidence = float(probs[predicted_class])
            label = self._label_map.get(predicted_class, "unknown")

            # Determine if safe
            is_injection = (label == "injection" and confidence >= settings.safety.classifier.threshold)

            return ClassifierResult(
                is_safe=not is_injection,
                confidence=confidence,
                label=label,
            )

        except Exception as e:
            logger.error(f"Classifier inference error: {e}")
            # On error, fail open (let other layers handle it)
            return ClassifierResult(is_safe=True, confidence=0.0, label="error")
