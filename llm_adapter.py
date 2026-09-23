"""
Universal Model-Agnostic LLM Adapter
Supports Groq, Azure OpenAI, Mistral, OpenAI, Google Gemini, Ollama, OpenRouter, and Custom Endpoints.
Provides a unified interface for chat completions, tool execution (MCP), and dynamic configuration.
"""

import os
import json
import logging
import time
import httpx
from typing import Dict, Any, List, Optional, Tuple
from storage_config import LLM_CONFIG_PATH, BASE_DIR

logger = logging.getLogger("llm_adapter")
CONFIG_FILE = LLM_CONFIG_PATH
ENV_FILE = os.path.join(BASE_DIR, ".env")

PROVIDER_PRESETS = {
    "groq": {
        "name": "Groq Cloud (Ultra Fast)",
        "base_url": "https://api.groq.com/openai/v1/chat/completions",
        "default_model": "llama-3.3-70b-versatile",
        "supported_models": ["llama-3.3-70b-versatile", "mixtral-8x7b-32768", "llama-3.1-8b-instant"],
        "auth_type": "bearer",
        "env_key": "GROQ_API_KEY"
    },
    "openai": {
        "name": "OpenAI Official",
        "base_url": "https://api.openai.com/v1/chat/completions",
        "default_model": "gpt-4o-mini",
        "supported_models": ["gpt-4o-mini", "gpt-4o", "o3-mini", "gpt-4-turbo"],
        "auth_type": "bearer",
        "env_key": "OPENAI_API_KEY"
    },
    "azure_openai": {
        "name": "Azure OpenAI Service",
        "base_url": "https://{resource}.openai.azure.com/openai/deployments/{deployment}/chat/completions?api-version=2024-02-15-preview",
        "default_model": "gpt-4o-mini",
        "supported_models": ["gpt-4o-mini", "gpt-4o", "gpt-35-turbo"],
        "auth_type": "azure_header",
        "env_key": "AZURE_OPENAI_KEY"
    },
    "mistral": {
        "name": "Mistral AI",
        "base_url": "https://api.mistral.ai/v1/chat/completions",
        "default_model": "mistral-small-latest",
        "supported_models": ["mistral-small-latest", "open-mistral-nemo", "codestral-latest", "mistral-large-latest"],
        "auth_type": "bearer",
        "env_key": "MISTRAL_API_KEY"
    },
    "gemini": {
        "name": "Google Gemini (AI Studio)",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "default_model": "gemini-1.5-flash",
        "supported_models": ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.0-flash-exp"],
        "auth_type": "bearer",
        "env_key": "GEMINI_API_KEY"
    },
    "ollama": {
        "name": "Self-Hosted Ollama / Azure VM",
        "base_url": "http://172.198.58.85:11434/v1/chat/completions",
        "default_model": "qwen2.5-coder:1.5b",
        "supported_models": ["qwen2.5-coder:1.5b", "mistral:7b", "llama3.2:3b", "phi3:mini"],
        "auth_type": "none",
        "env_key": "OLLAMA_API_KEY"
    },
    "openrouter": {
        "name": "OpenRouter (Multi-Model)",
        "base_url": "https://openrouter.ai/api/v1/chat/completions",
        "default_model": "meta-llama/llama-3.3-70b-instruct",
        "supported_models": ["meta-llama/llama-3.3-70b-instruct", "mistralai/mistral-large-2407", "anthropic/claude-3.5-sonnet"],
        "auth_type": "bearer",
        "env_key": "OPENROUTER_API_KEY"
    },
    "custom": {
        "name": "Custom OpenAI-Compatible Endpoint",
        "base_url": "",
        "default_model": "default",
        "supported_models": [],
        "auth_type": "bearer",
        "env_key": "CUSTOM_LLM_KEY"
    }
}


class UniversalLLMClient:
    """Model-Agnostic Universal LLM Client for Dynamic Autonomous Agents & MCP Tools."""

    def __init__(self):
        self.config = self.load_config()

    def load_config(self) -> Dict[str, Any]:
        """Loads active LLM configuration from config.json or environment."""
        cfg = {
            "provider": os.environ.get("LLM_PROVIDER", "groq"),
            "endpoint": os.environ.get("LLM_ENDPOINT", ""),
            "api_key": os.environ.get("LLM_API_KEY", ""),
            "model": os.environ.get("LLM_MODEL", ""),
            "temperature": float(os.environ.get("LLM_TEMPERATURE", "0.2")),
            "max_tokens": int(os.environ.get("LLM_MAX_TOKENS", "4096")),
            "timeout_seconds": int(os.environ.get("LLM_TIMEOUT", "60"))
        }

        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    file_cfg = json.load(f)
                    llm_cfg = file_cfg.get("llm_config", {})
                    for k, v in llm_cfg.items():
                        if v is not None and v != "":
                            cfg[k] = v
            except Exception as e:
                logger.warning(f"Could not read config.json: {e}")

        # Fill defaults based on provider preset if missing
        provider = cfg.get("provider", "groq")
        preset = PROVIDER_PRESETS.get(provider, PROVIDER_PRESETS["groq"])
        if not cfg.get("endpoint"):
            cfg["endpoint"] = preset["base_url"]
        if not cfg.get("model"):
            cfg["model"] = preset["default_model"]
        if not cfg.get("api_key"):
            cfg["api_key"] = os.environ.get(preset.get("env_key", ""), "")

        return cfg

    def save_config(self, new_config: Dict[str, Any]) -> Tuple[bool, str]:
        """Saves dynamic LLM settings into config.json and active runtime."""
        try:
            if not new_config.get("api_key") or str(new_config.get("api_key", "")).startswith("•"):
                new_config["api_key"] = self.config.get("api_key", "")
            self.config.update(new_config)

            # Update config.json
            full_cfg = {}
            if os.path.exists(CONFIG_FILE):
                try:
                    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                        full_cfg = json.load(f)
                except Exception:
                    full_cfg = {}

            full_cfg["llm_config"] = self.config
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(full_cfg, f, indent=2)

            # Update environment variables
            os.environ["LLM_PROVIDER"] = str(self.config.get("provider", ""))
            os.environ["LLM_ENDPOINT"] = str(self.config.get("endpoint", ""))
            os.environ["LLM_API_KEY"] = str(self.config.get("api_key", ""))
            os.environ["LLM_MODEL"] = str(self.config.get("model", ""))

            return True, "LLM configuration successfully saved and applied."
        except Exception as e:
            logger.error(f"Failed to save LLM config: {e}")
            return False, str(e)

    def _prepare_request_headers_and_url(self) -> Tuple[str, Dict[str, str]]:
        """Prepares headers and standardizes the URL based on provider type."""
        provider = self.config.get("provider", "groq")
        endpoint = self.config.get("endpoint", "").strip()
        api_key = self.config.get("api_key", "").strip()

        # If endpoint is missing or base domain only, normalize to /chat/completions
        if not endpoint:
            preset = PROVIDER_PRESETS.get(provider, PROVIDER_PRESETS["groq"])
            endpoint = preset["base_url"]

        clean_endpoint = endpoint.rstrip("/")
        if not clean_endpoint.endswith("/chat/completions") and not "?" in clean_endpoint:
            endpoint = clean_endpoint + "/chat/completions"
        else:
            endpoint = clean_endpoint

        headers = {
            "Content-Type": "application/json"
        }

        # Authentication Strategy
        if provider == "azure_openai" or "openai.azure.com" in endpoint:
            headers["api-key"] = api_key
        elif api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        return endpoint, headers

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Executes a chat completion call with universal normalization across LLM providers.
        Returns standard format:
        {
            "content": "text response",
            "tool_calls": [...],
            "raw_response": {...},
            "model": "model_used",
            "provider": "provider_used"
        }
        """
        endpoint, headers = self._prepare_request_headers_and_url()
        model = self.config.get("model", "llama-3.3-70b-versatile")
        temp = temperature if temperature is not None else self.config.get("temperature", 0.2)
        tokens = max_tokens if max_tokens is not None else self.config.get("max_tokens", 4096)
        timeout_sec = timeout if timeout is not None else self.config.get("timeout_seconds", 60)

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temp,
            "max_tokens": tokens
        }

        if tools and len(tools) > 0:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        logger.info(f"Invoking Universal LLM: Provider={self.config.get('provider')} | Model={model} | URL={endpoint}")

        with httpx.Client(timeout=timeout_sec) as client:
            response = client.post(endpoint, headers=headers, json=payload)
            if response.status_code != 200:
                try:
                    err_json = response.json()
                    detail = (
                        err_json.get("error", {}).get("message")
                        if isinstance(err_json.get("error"), dict)
                        else err_json.get("error") or err_json.get("detail") or str(err_json)
                    )
                except Exception:
                    detail = response.text[:200]
                err_msg = f"HTTP {response.status_code}: {detail}"
                logger.error(err_msg)
                raise Exception(err_msg)

            data = response.json()
            choice = data.get("choices", [{}])[0]
            msg = choice.get("message", {})

            return {
                "content": msg.get("content", "") or "",
                "tool_calls": msg.get("tool_calls", []),
                "role": msg.get("role", "assistant"),
                "raw_response": data,
                "model": model,
                "provider": self.config.get("provider")
            }

    def test_connection(self) -> Dict[str, Any]:
        """Runs a diagnostic ping to test provider credentials, latency, and model availability."""
        t0 = time.time()
        try:
            res = self.chat_completion(
                messages=[
                    {"role": "system", "content": "You are a DevOps assistant. Answer in 5 words."},
                    {"role": "user", "content": "Ping test"}
                ],
                max_tokens=20,
                timeout=10
            )
            latency_ms = int((time.time() - t0) * 1000)
            return {
                "status": "online",
                "provider": self.config.get("provider"),
                "model": res.get("model"),
                "latency_ms": latency_ms,
                "sample_response": res.get("content", "").strip(),
                "message": f"Successfully connected to {self.config.get('provider')} ({latency_ms}ms)"
            }
        except Exception as e:
            latency_ms = int((time.time() - t0) * 1000)
            return {
                "status": "error",
                "provider": self.config.get("provider"),
                "model": self.config.get("model"),
                "latency_ms": latency_ms,
                "message": str(e)
            }


# Global Singleton Instance
llm_client = UniversalLLMClient()
