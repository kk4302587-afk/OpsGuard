"""Layer 1: Rule-based safety filter.

Fast regex/keyword matching to block obvious attacks.
Zero latency, catches ~80% of known attack patterns.
"""

import re
from dataclasses import dataclass

from loguru import logger

from app.config import settings


@dataclass
class RuleCheckResult:
    """Result of a rule engine check."""
    is_safe: bool
    matched_rule: str | None = None
    category: str | None = None  # "injection" or "dangerous_command"


class RuleEngine:
    """Rule-based input filter using regex patterns."""

    def __init__(self):
        self._injection_patterns: list[re.Pattern] = []
        self._command_patterns: list[re.Pattern] = []
        self._high_risk_intent_patterns: list[re.Pattern] = []
        self._load_patterns()

    def _load_patterns(self):
        """Load patterns from configuration."""
        rules_config = settings.safety.rules

        for pattern in rules_config.injection_patterns:
            try:
                self._injection_patterns.append(re.compile(pattern, re.IGNORECASE))
            except re.error as e:
                logger.warning(f"Invalid injection pattern '{pattern}': {e}")

        for pattern in rules_config.blocked_patterns:
            try:
                self._command_patterns.append(re.compile(pattern, re.IGNORECASE))
            except re.error as e:
                logger.warning(f"Invalid command pattern '{pattern}': {e}")

        for pattern in rules_config.high_risk_intent_patterns:
            try:
                self._high_risk_intent_patterns.append(re.compile(pattern, re.IGNORECASE))
            except re.error as e:
                logger.warning(f"Invalid high-risk intent pattern '{pattern}': {e}")

        logger.info(
            f"Rule engine loaded: {len(self._injection_patterns)} injection patterns, "
            f"{len(self._command_patterns)} command patterns, "
            f"{len(self._high_risk_intent_patterns)} high-risk intent patterns"
        )

    def check_input(self, text: str) -> RuleCheckResult:
        """Check user input for injection attempts."""
        for pattern in self._injection_patterns:
            if pattern.search(text):
                logger.warning(f"Injection pattern matched: {pattern.pattern}")
                return RuleCheckResult(
                    is_safe=False,
                    matched_rule=pattern.pattern,
                    category="injection",
                )
        return RuleCheckResult(is_safe=True)

    def check_command(self, command: str) -> RuleCheckResult:
        """Check a generated command for dangerous patterns."""
        for pattern in self._command_patterns:
            if pattern.search(command):
                logger.warning(f"Dangerous command pattern matched: {pattern.pattern}")
                return RuleCheckResult(
                    is_safe=False,
                    matched_rule=pattern.pattern,
                    category="dangerous_command",
                )
        return RuleCheckResult(is_safe=True)

    def check_high_risk_intent(self, text: str) -> RuleCheckResult:
        """Check if user input expresses a high-risk intent.

        Unlike injection detection, this doesn't block the request.
        It flags it so the Agent knows to be extra cautious and confirm with the user.
        """
        for pattern in self._high_risk_intent_patterns:
            if pattern.search(text):
                return RuleCheckResult(
                    is_safe=False,
                    matched_rule=pattern.pattern,
                    category="high_risk_intent",
                )
        return RuleCheckResult(is_safe=True)
