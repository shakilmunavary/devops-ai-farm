"""
Platform Specifications Registry (100% Dynamic & Tool-Agnostic)
No hardcoded tools or platforms. All MCP servers are dynamically synthesized by Mistral AI on-demand.
"""

from typing import Dict, Any, List, Optional
import os
import json
from storage_config import CONFIG_JSON_PATH, BASE_DIR


def get_all_platforms() -> List[Dict[str, Any]]:
    """
    Returns list of active, dynamically generated servers.
    No hardcoded platforms.
    """
    platforms = []
    if os.path.exists(CONFIG_JSON_PATH):
        try:
            with open(CONFIG_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                for s_id, s in data.get("servers", {}).items():
                    platforms.append({
                        "id": s.get("id", s_id),
                        "name": s.get("name", s_id.title()),
                        "category": s.get("category", "Custom MCP Server"),
                        "description": s.get("description", ""),
                        "fields": s.get("fields", []),
                        "tools": s.get("tools", [])
                    })
        except Exception:
            pass
    return platforms


def get_platform_spec(platform_id: str) -> Optional[Dict[str, Any]]:
    """
    Lookup a dynamically registered platform specification by ID.
    """
    platform_id = (platform_id or "").lower().strip()
    if not platform_id:
        return None
    for p in get_all_platforms():
        if p["id"].lower() == platform_id:
            return p
    return None


def find_platform_by_query(query: str) -> Optional[Dict[str, Any]]:
    """
    Pure tool-agnostic query lookup. Returns existing dynamically generated platform if present.
    """
    q = (query or "").lower().strip()
    if not q:
        return None
    for p in get_all_platforms():
        if p["id"].lower() == q or p["name"].lower() == q:
            return p
    return None
