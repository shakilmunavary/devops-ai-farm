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
    """Initializes required persistent folders and syncs baseline server tools into persistent storage."""
    os.makedirs(PERSISTENT_DATA_DIR, exist_ok=True)
    os.makedirs(BOTS_DIR, exist_ok=True)
    os.makedirs(SERVERS_DIR, exist_ok=True)
    os.makedirs(GATEWAY_CONFIG_DIR, exist_ok=True)

    baseline_candidates = [
        os.path.join(BASE_DIR, "persistent_data", "config.json"),
        os.path.join(BASE_DIR, "config.json"),
    ]
    baseline_cfg = {}
    for bp in baseline_candidates:
        if os.path.exists(bp):
            try:
                with open(bp, "r", encoding="utf-8") as bf:
                    loaded = json.load(bf)
                    if loaded.get("servers"):
                        baseline_cfg = loaded
                        break
            except Exception:
                pass

    # 1. Initialize or merge config.json
    if not os.path.exists(CONFIG_JSON_PATH):
        try:
            init_data = baseline_cfg if baseline_cfg else {"servers": {}}
            with open(CONFIG_JSON_PATH, "w", encoding="utf-8") as f:
                json.dump(init_data, f, indent=2)
            logger.info(f"Initialized persistent config.json from baseline at {CONFIG_JSON_PATH}")
        except Exception as e:
            logger.warning(f"Could not create persistent config.json: {e}")
    else:
        # Merge new tools from baseline into existing persistent config.json
        if baseline_cfg and baseline_cfg.get("servers"):
            try:
                with open(CONFIG_JSON_PATH, "r", encoding="utf-8") as f:
                    target_cfg = json.load(f)

                target_servers = target_cfg.get("servers", {})
                modified = False

                for s_id, b_srv in baseline_cfg.get("servers", {}).items():
                    if s_id in target_servers:
                        t_srv = target_servers[s_id]
                        t_tool_names = {t["name"] for t in t_srv.get("tools", []) if isinstance(t, dict) and t.get("name")}
                        all_tool_names = {t["name"] for t in t_srv.get("all_tools", []) if isinstance(t, dict) and t.get("name")}
                        enabled = set(t_srv.get("enabled_tools", list(t_tool_names)))

                        for bt in b_srv.get("tools", []):
                            if isinstance(bt, dict) and bt.get("name"):
                                if bt["name"] not in t_tool_names:
                                    t_srv.setdefault("tools", []).append(bt)
                                    t_tool_names.add(bt["name"])
                                    modified = True
                                if bt["name"] not in all_tool_names:
                                    t_srv.setdefault("all_tools", []).append(bt)
                                    all_tool_names.add(bt["name"])
                                    modified = True
                                if bt["name"] not in enabled:
                                    enabled.add(bt["name"])
                                    modified = True

                        t_srv["enabled_tools"] = list(enabled)
                        target_servers[s_id] = t_srv

                if modified:
                    target_cfg["servers"] = target_servers
                    with open(CONFIG_JSON_PATH, "w", encoding="utf-8") as f:
                        json.dump(target_cfg, f, indent=2)
                    logger.info("Successfully merged new baseline tools into persistent config.json!")
            except Exception as e:
                logger.warning(f"Error merging baseline config into persistent storage: {e}")

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
