"""
Centralized Persistent Storage Configuration
Ensures MCP servers, credentials, and AI bots are preserved across all container restarts,
reboots, and Azure DevOps CI/CD pipeline deployments.
"""

import os
import sys
import shutil
import json
import logging

logger = logging.getLogger("storage_config")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def get_persistent_data_dir() -> str:
    explicit = os.environ.get("PERSISTENT_DATA_DIR")
    if explicit:
        return explicit

    if os.path.exists("/home") and os.path.isdir("/home"):
        target = "/home/data/mcp_storage"
        try:
            os.makedirs(target, exist_ok=True)
            return target
        except Exception:
            pass

    local_data = os.path.join(BASE_DIR, "persistent_data")
    os.makedirs(local_data, exist_ok=True)
    return local_data


PERSISTENT_DATA_DIR = get_persistent_data_dir()
CONFIG_JSON_PATH = os.path.join(PERSISTENT_DATA_DIR, "config.json")
BOTS_DIR = os.path.join(PERSISTENT_DATA_DIR, "mcp_bots")
SERVERS_DIR = os.path.join(PERSISTENT_DATA_DIR, "mcp_servers")
LLM_CONFIG_PATH = os.path.join(PERSISTENT_DATA_DIR, "llm_config.json")
GATEWAY_CONFIG_DIR = os.path.join(PERSISTENT_DATA_DIR, "mcp_gateway")
GATEWAY_CONFIG_PATH = os.path.join(GATEWAY_CONFIG_DIR, "config.yaml")


def init_persistent_storage():
    """Initializes required persistent folders and default files without auto-seeding sample bots or servers."""
    os.makedirs(PERSISTENT_DATA_DIR, exist_ok=True)
    os.makedirs(BOTS_DIR, exist_ok=True)
    os.makedirs(SERVERS_DIR, exist_ok=True)
    os.makedirs(GATEWAY_CONFIG_DIR, exist_ok=True)

    # 1. Initialize empty config.json if not present
    if not os.path.exists(CONFIG_JSON_PATH):
        try:
            with open(CONFIG_JSON_PATH, "w", encoding="utf-8") as f:
                json.dump({"servers": {}}, f, indent=2)
            logger.info(f"Initialized clean persistent config.json at {CONFIG_JSON_PATH}")
        except Exception as e:
            logger.warning(f"Could not create persistent config.json: {e}")

    # 2. Initialize default llm_config.json if not present
    if not os.path.exists(LLM_CONFIG_PATH):
        baseline_llm = os.path.join(BASE_DIR, "llm_config.json")
        if os.path.exists(baseline_llm):
            try:
                shutil.copyfile(baseline_llm, LLM_CONFIG_PATH)
                logger.info(f"Copied baseline llm_config.json to {LLM_CONFIG_PATH}")
            except Exception as e:
                logger.warning(f"Could not copy baseline llm_config.json: {e}")
        else:
            try:
                with open(LLM_CONFIG_PATH, "w", encoding="utf-8") as f:
                    json.dump({"provider": "mistral", "mistral_api_key": ""}, f, indent=2)
            except Exception:
                pass


init_persistent_storage()
