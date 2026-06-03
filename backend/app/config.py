"""Application configuration loaded from config.yaml."""

import os
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

# Load .env file
load_dotenv(Path(__file__).parent.parent / ".env")


class AppConfig(BaseModel):
    name: str = "OpsGuard"
    version: str = "0.1.0"
    debug: bool = True
    host: str = "0.0.0.0"
    port: int = 8000


class LLMModelConfig(BaseModel):
    provider: str = "openai"
    model: str = "qwen3-8b"
    api_base: str = ""
    api_key: str = ""
    temperature: float = 0.7
    max_tokens: int = 4096


class LLMConfig(BaseModel):
    primary: LLMModelConfig = LLMModelConfig()
    fallback: Optional[LLMModelConfig] = None


class RulesConfig(BaseModel):
    enabled: bool = True
    blocked_patterns: list[str] = []
    injection_patterns: list[str] = []
    high_risk_intent_patterns: list[str] = []


class ClassifierConfig(BaseModel):
    enabled: bool = True
    model_path: str = "./models/prompt_guard"
    threshold: float = 0.85
    fallback_on_error: bool = True


class LLMConstraintsConfig(BaseModel):
    enabled: bool = True


class SafetyConfig(BaseModel):
    rules: RulesConfig = RulesConfig()
    classifier: ClassifierConfig = ClassifierConfig()
    llm_constraints: LLMConstraintsConfig = LLMConstraintsConfig()


class ExecutionConfig(BaseModel):
    run_as_user: str = "opsguard"
    sudo_whitelist: list[str] = []
    protected_paths: list[str] = []
    timeout: int = 30
    auto_backup: bool = True
    backup_dir: str = "/var/lib/opsguard/backups"


class PolicyRuleConfig(BaseModel):
    name: str = ""
    description: str = ""
    action: str = "allow"
    tools: list[str] = []
    categories: list[str] = []
    paths: list[str] = []
    services: list[str] = []
    users: list[str] = []
    hosts: list[str] = []
    environments: list[str] = []
    risk_levels: list[str] = []


class PolicyConfig(BaseModel):
    enabled: bool = True
    environment: str = "development"
    host: str = ""
    allowed_write_paths: list[str] = []
    denied_paths: list[str] = []
    protected_services: list[str] = []
    maintenance_windows: list[str] = []
    max_blast_radius: int = 1
    enforce_sudo_allowlist: bool = False
    rules: list[PolicyRuleConfig] = []


class KnowledgeConfig(BaseModel):
    db_path: str = "./data/knowledge.db"
    auto_save: bool = True


class AuditConfig(BaseModel):
    db_path: str = "./data/audit.db"
    retention_days: int = 90


class WebSocketConfig(BaseModel):
    heartbeat_interval: int = 30
    max_message_size: int = 1048576


class MCPConfig(BaseModel):
    enabled: bool = False
    transport: str = "stdio"
    command: str = ""
    args: list[str] = Field(default_factory=lambda: ["-m", "app.mcp_server"])
    timeout: float = 30.0
    fallback_to_local: bool = True


class ObservabilityConfig(BaseModel):
    prometheus_base_url: str = ""
    loki_base_url: str = ""
    timeout: float = 10.0
    default_range_minutes: int = 30
    max_log_limit: int = 100


class Settings(BaseModel):
    app: AppConfig = AppConfig()
    llm: LLMConfig = LLMConfig()
    safety: SafetyConfig = SafetyConfig()
    execution: ExecutionConfig = ExecutionConfig()
    policy: PolicyConfig = PolicyConfig()
    knowledge: KnowledgeConfig = KnowledgeConfig()
    audit: AuditConfig = AuditConfig()
    websocket: WebSocketConfig = WebSocketConfig()
    mcp: MCPConfig = MCPConfig()
    observability: ObservabilityConfig = ObservabilityConfig()


def _resolve_env_vars(value: str) -> str:
    """Resolve ${ENV_VAR} patterns in string values."""
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        env_var = value[2:-1]
        return os.environ.get(env_var, "")
    return value


def _resolve_dict(d: dict) -> dict:
    """Recursively resolve environment variables in a dict."""
    resolved = {}
    for key, value in d.items():
        if isinstance(value, dict):
            resolved[key] = _resolve_dict(value)
        elif isinstance(value, list):
            resolved[key] = [_resolve_env_vars(v) if isinstance(v, str) else v for v in value]
        elif isinstance(value, str):
            resolved[key] = _resolve_env_vars(value)
        else:
            resolved[key] = value
    return resolved


def load_settings() -> Settings:
    """Load settings from config.yaml with environment variable resolution."""
    config_path = Path(__file__).parent.parent / "config.yaml"

    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            raw_config = yaml.safe_load(f) or {}
        config = _resolve_dict(raw_config)
        return Settings(**config)

    return Settings()


# Global settings instance
settings = load_settings()
