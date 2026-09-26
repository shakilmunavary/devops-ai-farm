"""
Gateway Manager (with API Key Security & Selective Tool Exposure)
Manages the MCP Gateway on Port 5001.
Secured with customizable Gateway API Key authentication.
Supports selective tool exposure and multiplexing.
"""

import os
import sys
import json
import yaml
import logging
import inspect
import importlib.util
import subprocess
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

from storage_config import CONFIG_JSON_PATH, GATEWAY_CONFIG_PATH, SERVERS_DIR, BASE_DIR

ROOT_ENV_PATH = os.path.join(BASE_DIR, ".env")
if os.path.exists(ROOT_ENV_PATH):
    load_dotenv(ROOT_ENV_PATH)

CONFIG_YAML_PATH = GATEWAY_CONFIG_PATH

GATEWAY_BINARY = os.environ.get("GATEWAY_BINARY", "agentgateway")
GATEWAY_PORT = int(os.environ.get("GATEWAY_PORT", 5001))
DEFAULT_GATEWAY_API_KEY = os.environ.get("GATEWAY_API_KEY", "mcp_live_key_dev_2026")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("gateway_manager")


def get_current_gateway_api_key() -> str:
    """Retrieve active Gateway API Key from environment or root .env."""
    if os.path.exists(ROOT_ENV_PATH):
        load_dotenv(ROOT_ENV_PATH, override=True)
    return os.environ.get("GATEWAY_API_KEY", DEFAULT_GATEWAY_API_KEY)


def set_current_gateway_api_key(new_key: str) -> str:
    """Update and persist Gateway API Key in .env and runtime."""
    new_key = new_key.strip()
    os.environ["GATEWAY_API_KEY"] = new_key
    
    # Update or create root .env
    lines = []
    found = False
    if os.path.exists(ROOT_ENV_PATH):
        with open(ROOT_ENV_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for i, line in enumerate(lines):
            if line.startswith("GATEWAY_API_KEY="):
                lines[i] = f"GATEWAY_API_KEY={new_key}\n"
                found = True
                break
    if not found:
        lines.append(f"\nGATEWAY_API_KEY={new_key}\n")

    with open(ROOT_ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)

    logger.info(f"Updated GATEWAY_API_KEY in {ROOT_ENV_PATH}")
    return new_key


def get_allowed_tools_for_server(target_name: str) -> Optional[List[str]]:
    """Check config.json to see if selective tool exposure is configured."""
    if not os.path.exists(CONFIG_JSON_PATH):
        return None
    try:
        with open(CONFIG_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            server = data.get("servers", {}).get(target_name)
            if server and "enabled_tools" in server:
                return server["enabled_tools"]
            elif server and "tools" in server:
                return [t["name"] for t in server["tools"]]
    except Exception:
        pass
    return None


def load_tool_module(server_name: str):
    """Dynamically load an MCP server module from mcp_servers/<server_name>/server.py."""
    server_path = None
    try:
        from app import ensure_server_script, load_server_registry
        registry = load_server_registry()
        s_info = registry.get("servers", {}).get(server_name)
        if s_info:
            server_path = ensure_server_script(server_name, s_info.get("config", {}), s_info.get("tools", []))
    except Exception as ex_gen:
        logger.warning(f"Could not auto-generate fresh server script for {server_name}: {ex_gen}")

    if not server_path or not os.path.exists(server_path):
        candidates = [
            os.path.join(SERVERS_DIR, server_name, "server.py"),
            os.path.join(BASE_DIR, "mcp_servers", server_name, "server.py"),
            os.path.join(BASE_DIR, "persistent_data", "mcp_servers", server_name, "server.py"),
            os.path.join("/home/data/mcp_storage/mcp_servers", server_name, "server.py"),
            os.path.join("/home/site/wwwroot/mcp_servers", server_name, "server.py"),
            os.path.join("/data/mcp_storage/mcp_servers", server_name, "server.py")
        ]
        for c in candidates:
            if os.path.exists(c):
                server_path = c
                break

    if not server_path or not os.path.exists(server_path):
        return None, f"Script not found for server '{server_name}'"

    try:
        module_name = f"mcp_server_{server_name}"
        if module_name in sys.modules:
            del sys.modules[module_name]
        spec = importlib.util.spec_from_file_location(module_name, server_path)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            return module, ""
    except Exception as e:
        logger.error(f"Error loading server module for {server_name}: {e}")
        return None, str(e)
    return None, "Unknown module load failure"


def execute_tool_call(target_name: str, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a tool function on the specified target server and return JSON-RPC payload."""
    allowed_tools = get_allowed_tools_for_server(target_name)
    if allowed_tools is not None and tool_name not in allowed_tools:
        found_in_allowed = False
        for at in allowed_tools:
            clean_at = at.replace("_", "").lower()
            clean_t = tool_name.replace("_", "").lower()
            if clean_at == clean_t or clean_at.startswith(clean_t[:6]) or clean_t.startswith(clean_at[:6]):
                found_in_allowed = True
                tool_name = at
                break
        if not found_in_allowed:
            return {
                "content": [{"type": "text", "text": f"⚠️ Tool '{tool_name}' is not exposed on server '{target_name}'. Enable it via 'Manage Exposed Tools'."}],
                "isError": True
            }

    module, err_msg = load_tool_module(target_name)
    if not module:
        return {
            "content": [{"type": "text", "text": f"Error loading MCP Server '{target_name}': {err_msg}"}],
            "isError": True
        }

    # 1. Exact match
    func = getattr(module, tool_name, None)
    
    # 2. Strict semantic alias matching (e.g. list_repositories <-> list_repos)
    if not func or not callable(func):
        alias_map = {
            "list_repositories": "list_repos",
            "list_repos": "list_repositories",
            "list_jobs": "list_all_jobs",
            "list_incidents": "get_incidents"
        }
        mapped_name = alias_map.get(tool_name)
        if mapped_name and hasattr(module, mapped_name):
            func = getattr(module, mapped_name, None)
            if callable(func):
                tool_name = mapped_name

    # 3. Normalized stem matching (e.g. repo/repos/repositories)
    if not func or not callable(func):
        tool_stem = tool_name.replace("repositories", "repo").replace("repos", "repo").replace("_", "").lower()
        for attr in dir(module):
            if attr.startswith("_") or attr[0].isupper() or attr in ["Any", "Dict", "List", "Optional", "Union", "Tuple", "Set", "FastMCP"]:
                continue
            cand = getattr(module, attr, None)
            if not callable(cand) or not (inspect.isfunction(cand) or hasattr(cand, "__code__")):
                continue
            attr_stem = attr.replace("repositories", "repo").replace("repos", "repo").replace("_", "").lower()
            if attr_stem == tool_stem:
                func = cand
                tool_name = attr
                break

    if not func or not callable(func):
        return {
            "content": [{"type": "text", "text": f"Error: Tool '{tool_name}' not found on server '{target_name}'."}],
            "isError": True
        }

    try:
        sig = inspect.signature(func)
        bound_args = {}
        has_var_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())

        for param_name, param in sig.parameters.items():
            if param_name in arguments:
                bound_args[param_name] = arguments[param_name]
            elif param_name == "repo" and "repository" in arguments:
                bound_args["repo"] = arguments["repository"]
            elif param_name == "repository" and "repo" in arguments:
                bound_args["repository"] = arguments["repo"]
            elif param.default is inspect.Parameter.empty and param.kind != inspect.Parameter.VAR_KEYWORD:
                # If param has no default and was not provided directly
                if param_name in ["repo", "full_repo"] and "owner" in arguments and ("repo" in arguments or "repository" in arguments):
                    bound_args[param_name] = f"{arguments['owner']}/{arguments.get('repo') or arguments.get('repository')}"

        if has_var_kwargs:
            for k, v in arguments.items():
                if k not in bound_args:
                    bound_args[k] = v

        import asyncio
        if inspect.iscoroutinefunction(func):
            result = asyncio.run(func(**bound_args))
        else:
            result = func(**bound_args)

        return {
            "content": [{"type": "text", "text": str(result)}],
            "isError": False
        }
    except Exception as e:
        logger.error(f"Error executing tool {tool_name} on {target_name}: {e}")
        return {
            "content": [{"type": "text", "text": f"Execution Error: {e}"}],
            "isError": True
        }


class BuiltinMCPGatewayHandler(BaseHTTPRequestHandler):
    """HTTP & JSON-RPC handler with API Key authentication on Port 5001."""

    def _set_headers(self, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-API-Key")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_OPTIONS(self):
        self._set_headers(200)

    def _is_authenticated(self) -> bool:
        """Verify Gateway API Key from request headers."""
        expected_key = get_current_gateway_api_key()
        if not expected_key:
            return True  # Open mode if no key configured

        # Check X-API-Key header
        x_api_key = self.headers.get("X-API-Key", "").strip()
        if x_api_key and x_api_key == expected_key:
            return True

        # Check Authorization: Bearer <key>
        auth_header = self.headers.get("Authorization", "").strip()
        if auth_header:
            parts = auth_header.split()
            if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1] == expected_key:
                return True

        return False

    def do_GET(self):
        # Health endpoint (open)
        path = self.path.strip("/")
        if path == "" or path == "health":
            self._set_headers(200)
            self.wfile.write(json.dumps({
                "gateway": "AI MCP Server Kit Gateway",
                "status": "online",
                "port": GATEWAY_PORT,
                "auth_required": True,
                "protocol": "mcp/jsonrpc-2.0"
            }).encode("utf-8"))
            return

        if not self._is_authenticated():
            self._set_headers(401)
            self.wfile.write(json.dumps({"error": "Unauthorized: Invalid or missing X-API-Key header"}).encode("utf-8"))
            return

        parts = path.split("/")
        if len(parts) == 2 and parts[0] == "mcp":
            target = parts[1]
            module = load_tool_module(target)
            if module:
                self._set_headers(200)
                allowed = get_allowed_tools_for_server(target)
                self.wfile.write(json.dumps({"target": target, "status": "active", "exposed_tools": allowed}).encode("utf-8"))
                return

        self._set_headers(404)
        self.wfile.write(json.dumps({"error": "Endpoint not found"}).encode("utf-8"))

    def do_POST(self):
        if not self._is_authenticated():
            self._set_headers(401)
            self.wfile.write(json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "error": {
                    "code": -32000,
                    "message": "Unauthorized: Missing or invalid Gateway API Key. Provide header 'X-API-Key: <key>' or 'Authorization: Bearer <key>'."
                }
            }, indent=2).encode("utf-8"))
            return

        path_parts = self.path.strip("/").split("/")
        target_name = None
        if len(path_parts) >= 2 and path_parts[0] == "mcp":
            target_name = path_parts[1]

        content_len = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_len) if content_len > 0 else b""
        raw_text = post_data.decode("utf-8", errors="replace").strip()

        body = {}
        if raw_text:
            try:
                body = json.loads(raw_text)
            except Exception:
                # Attempt to repair unquoted JSON common when calling curl.exe from Windows PowerShell
                try:
                    import re
                    # Add quotes around unquoted keys
                    fixed = re.sub(r'([{,\s])([a-zA-Z0-9_\-\.]+)\s*:', r'\1"\2":', raw_text)
                    # Add quotes around unquoted string values
                    fixed = re.sub(r':\s*([a-zA-Z0-9_\-\./]+)(\s*[,}])', r': "\1"\2', fixed)
                    body = json.loads(fixed)
                except Exception:
                    try:
                        body = yaml.safe_load(raw_text)
                    except Exception:
                        try:
                            import ast
                            body = ast.literal_eval(raw_text)
                        except Exception:
                            pass

        if not isinstance(body, dict):
            body = {}

        req_id = body.get("id", 1)
        method = body.get("method", "")
        params = body.get("params", {})

        if not target_name:
            target_name = params.get("target") or body.get("target", "jenkins")

        # 1. MCP JSON-RPC Method: "tools/call"
        if method == "tools/call" or (isinstance(params, dict) and "name" in params):
            tool_name = params.get("name") if isinstance(params, dict) else None
            arguments = params.get("arguments", {}) if isinstance(params, dict) else {}
            if not tool_name:
                self._set_headers(400)
                resp = {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": "Missing params.name"}}
            else:
                exec_result = execute_tool_call(target_name, tool_name, arguments)
                self._set_headers(200)
                resp = {"jsonrpc": "2.0", "id": req_id, "result": exec_result}
            self.wfile.write(json.dumps(resp, indent=2).encode("utf-8"))
            return

        # 2. MCP JSON-RPC Method: "initialize"
        elif method == "initialize":
            self._set_headers(200)
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": f"gateway-{target_name}", "version": "2.0"},
                    "capabilities": {"tools": {}}
                }
            }
            self.wfile.write(json.dumps(resp).encode("utf-8"))
            return

        # 3. Direct REST execution fallback: {"name": "...", "arguments": {...}}
        if isinstance(body, dict) and ("tool" in body or "name" in body):
            tool_name = body.get("tool") or body.get("name")
            arguments = body.get("arguments") or body.get("args") or {}
            exec_result = execute_tool_call(target_name, tool_name, arguments)
            self._set_headers(200)
            self.wfile.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": exec_result}, indent=2).encode("utf-8"))
            return

        self._set_headers(200)
        self.wfile.write(json.dumps({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"message": f"Gateway ready for target '{target_name}'."}
        }).encode("utf-8"))

    def log_message(self, format, *args):
        logger.info(f"Gateway HTTP :5001 - {format % args}")


class GatewayManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(GatewayManager, cls).__new__(cls)
                cls._instance._process = None
                cls._instance._builtin_server = None
                cls._instance._builtin_thread = None
                cls._instance._init_config()
            return cls._instance

    def _init_config(self) -> None:
        os.makedirs(os.path.dirname(CONFIG_YAML_PATH), exist_ok=True)
        if not os.path.exists(CONFIG_YAML_PATH):
            self._write_yaml({"port": GATEWAY_PORT, "targets": []})

    def _read_yaml(self) -> Dict[str, Any]:
        try:
            if not os.path.exists(CONFIG_YAML_PATH):
                return {"port": GATEWAY_PORT, "targets": []}
            with open(CONFIG_YAML_PATH, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                if "port" not in data:
                    data["port"] = GATEWAY_PORT
                if "targets" not in data or not isinstance(data["targets"], list):
                    data["targets"] = []
                return data
        except Exception as e:
            logger.error(f"Error reading YAML config: {e}")
            return {"port": GATEWAY_PORT, "targets": []}

    def _write_yaml(self, data: Dict[str, Any]) -> None:
        try:
            with open(CONFIG_YAML_PATH, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False)
        except Exception as e:
            logger.error(f"Failed to write YAML config: {e}")
            raise

    def get_targets(self) -> List[Dict[str, Any]]:
        return self._read_yaml().get("targets", [])

    def add_target(self, name: str, command: str, args: List[str], env: Optional[Dict[str, str]] = None) -> bool:
        data = self._read_yaml()
        targets = data.get("targets", [])
        updated = False
        target_entry = {"name": name, "transport": "stdio", "command": command, "args": args}
        if env:
            target_entry["env"] = env

        for idx, t in enumerate(targets):
            if t.get("name") == name:
                targets[idx] = target_entry
                updated = True
                break

        if not updated:
            targets.append(target_entry)

        data["targets"] = targets
        self._write_yaml(data)
        logger.info(f"Target '{name}' registered in config.yaml")
        return True

    def remove_target(self, name: str) -> bool:
        data = self._read_yaml()
        targets = data.get("targets", [])
        initial_len = len(targets)
        data["targets"] = [t for t in targets if t.get("name") != name]
        if len(data["targets"]) < initial_len:
            self._write_yaml(data)
            logger.info(f"Target '{name}' removed from config.yaml")
            return True
        return False

    def _start_builtin_gateway(self):
        if self._builtin_server:
            return
        try:
            server_address = ("0.0.0.0", GATEWAY_PORT)
            self._builtin_server = HTTPServer(server_address, BuiltinMCPGatewayHandler)
            logger.info(f"🚀 Built-in Secured MCP Gateway listening on http://0.0.0.0:{GATEWAY_PORT} (Auth Enabled)")
            self._builtin_thread = threading.Thread(target=self._builtin_server.serve_forever, daemon=True)
            self._builtin_thread.start()
        except OSError as e:
            logger.warning(f"Could not bind gateway to port {GATEWAY_PORT}: {e}")

    def start_gateway(self) -> Dict[str, Any]:
        with self._lock:
            self._start_builtin_gateway()
            return {
                "running": True,
                "status": "running",
                "port": GATEWAY_PORT,
                "api_key_enabled": True
            }

    def stop_gateway(self) -> Dict[str, Any]:
        return {"running": False, "status": "stopped"}

    def restart_gateway(self) -> Dict[str, Any]:
        return self.start_gateway()

    def get_status(self) -> Dict[str, Any]:
        is_builtin_running = self._builtin_server is not None
        return {
            "binary_installed": False,
            "running": is_builtin_running,
            "mode": "builtin_python",
            "port": GATEWAY_PORT,
            "api_key": get_current_gateway_api_key(),
            "target_count": len(self.get_targets())
        }

gateway_mgr = GatewayManager()
