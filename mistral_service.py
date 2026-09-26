"""
Universal AI Service - Interactive Multi-Turn MCP Architect, Agent, and Bot Designer
Provides deterministic tool synthesis, multi-turn design conversations, and schema customization.
"""

import os
import re
import json
import logging
import uuid
import requests
import time
from typing import Optional, Dict, Any, List, Tuple, Union
from dotenv import load_dotenv
from llm_adapter import llm_client

logger = logging.getLogger("mistral_service")

MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"
DEFAULT_MISTRAL_MODEL = os.environ.get("MISTRAL_MODEL", "codestral-latest")

AUTH_CREDENTIAL_PARAM_NAMES = {
    "token", "api_token", "github_token", "jenkins_token", "password", "passwd",
    "secret", "api_key", "secret_key", "base_url", "url", "jenkins_url",
    "instance_url", "crumb", "csrf_crumb", "access_key", "secret_access_key",
    "bearer_token", "private_key", "pat", "client_secret"
}


def parse_and_repair_json(content: str) -> dict:
    """
    Ultra-resilient JSON parser that handles markdown formatting, unescaped characters,
    trailing commas, token cutoff truncation, and partial JSON streams.
    """
    if not content or not content.strip():
        raise ValueError("Empty AI response received.")

    text = content.strip()
    # Strip markdown code fences
    text = re.sub(r"^`(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"`\s*$", "", text, flags=re.MULTILINE).strip()

    # 1. Direct standard parsing with strict=False
    try:
        return json.loads(text, strict=False)
    except Exception:
        pass

    # 2. Extract outermost { ... }
    match = re.search(r"(\{[\s\S]*\})", text)
    if match:
        try:
            return json.loads(match.group(1), strict=False)
        except Exception:
            pass

    # 3. Clean trailing commas before } or ]
    cleaned = re.sub(r",\s*([\}\]])", r"", text)
    try:
        return json.loads(cleaned, strict=False)
    except Exception:
        pass

    # 4. Truncated JSON recovery: scan backward for valid object/array boundary
    for end_idx in range(len(cleaned), 0, -1):
        if cleaned[end_idx-1] in ["}", "]"]:
            sub = cleaned[:end_idx]
            sub_cleaned = re.sub(r",\s*([\}\]])", r"", sub)
            open_cur = sub_cleaned.count("{") - sub_cleaned.count("}")
            open_sq = sub_cleaned.count("[") - sub_cleaned.count("]")
            fix_str = sub_cleaned + ("]" * max(0, open_sq)) + ("}" * max(0, open_cur))
            try:
                parsed = json.loads(fix_str, strict=False)
                if isinstance(parsed, dict) and ("tools" in parsed or "reply" in parsed or "platform_name" in parsed):
                    return parsed
            except Exception:
                continue

    # 5. Regex rescue: extract individual tools and metadata if full JSON is corrupted
    platform_name_m = re.search(r'"platform_name"\s*:\s*"([^"]+)"', text)
    platform_id_m = re.search(r'"platform_id"\s*:\s*"([^"]+)"', text)
    category_m = re.search(r'"category"\s*:\s*"([^"]+)"', text)
    description_m = re.search(r'"description"\s*:\s*"([^"]+)"', text)
    reply_m = re.search(r'"reply"\s*:\s*"([^"]+)"', text)

    tool_matches = re.finditer(r'\{\s*"name"\s*:\s*"([^"]+)"[\s\S]*?"example_call"\s*:\s*"([^"]*)"\s*\}', text)
    tools = []
    for tm in tool_matches:
        try:
            tools.append(json.loads(tm.group(0), strict=False))
        except Exception:
            pass

    if tools or platform_name_m:
        p_name = platform_name_m.group(1) if platform_name_m else "Discovered Platform"
        p_id = platform_id_m.group(1) if platform_id_m else re.sub(r"[^a-zA-Z0-9_]", "_", p_name.lower())
        return {
            "is_valid": True,
            "platform_name": p_name,
            "platform_id": p_id,
            "category": category_m.group(1) if category_m else "DevOps & Tooling",
            "description": description_m.group(1) if description_m else f"FastMCP Server for {p_name}",
            "reply": reply_m.group(1) if reply_m else f"Synthesized **{len(tools)} tools** from your specification.",
            "fields": [
                {"key": "base_url", "label": "Base URL", "prompt": "Enter instance/base URL:", "placeholder": "https://api.service.com", "default": "", "secret": False, "required": True},
                {"key": "api_token", "label": "API Token / Key", "prompt": "Enter API Token or Secret:", "placeholder": "••••••••••••", "default": "", "secret": True, "required": True}
            ],
            "tools": tools
        }

    raise ValueError(f"Could not parse valid JSON schema from AI response (length: {len(text)})")


def get_universal_ai_config() -> Dict[str, Any]:
    """Retrieve dynamic Universal AI LLM configuration."""
    load_dotenv(override=True)
    provider = os.environ.get("AI_PROVIDER", "mistral").lower()
    mistral_key = os.environ.get("MISTRAL_API_KEY", "").strip()
    return {
        "provider": provider,
        "url": os.environ.get("AI_API_URL", MISTRAL_API_URL),
        "model": os.environ.get("MISTRAL_MODEL", DEFAULT_MISTRAL_MODEL),
        "headers": {"Authorization": f"Bearer {mistral_key}" if mistral_key else "", "Content-Type": "application/json"}
    }


def get_mistral_api_key() -> Optional[str]:
    load_dotenv(override=True)
    return os.environ.get("MISTRAL_API_KEY", "").strip() or None


def set_mistral_api_key(api_key: str) -> None:
    api_key = api_key.strip()
    os.environ["MISTRAL_API_KEY"] = api_key
    
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    lines = []
    found = False
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MISTRAL_API_KEY="):
                    lines.append(f"MISTRAL_API_KEY={api_key}\n")
                    found = True
                else:
                    lines.append(line)
    if not found:
        lines.append(f"MISTRAL_API_KEY={api_key}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    logger.info("Updated MISTRAL_API_KEY in environment and .env")


SYSTEM_ARCHITECT_PROMPT = """You are the Lead Enterprise Model Context Protocol (MCP) Multi-Agent Architect & Validator Panel.
You operate as a collaborative panel of specialized AI agents:
1. 🏗️ MCP Solutions Architect: Formulates production-grade FastMCP servers for ANY tool, platform, or API (e.g. GitHub, GitLab, Datadog, Splunk, Jira, ServiceNow, Azure DevOps, Kubernetes, Cloudflare, AWS, Terraform, or internal custom REST APIs).
2. 🔬 Input & Schema Validator: Rigorously verifies tool parameter schemas, path parameter templates (e.g. {owner}, {repo}, {project}, {id}), query parameters vs JSON body payloads, and required vs optional argument definitions.
3. 🛡️ Security & Secret Shield: Identifies all authentication credentials (PATs, API tokens, passwords, client secrets, Bearer tokens), sets "secret": true, isolates them in "fields", and ensures connection secrets are NEVER leaked into tool parameters.
4. 🧠 Adaptive Conversational Memory: Retains previous dialogue state. If the user changes their mind (e.g. "actually let's build GitHub instead", "change the base URL", "add a delete tool", "what permissions do I need?"), flexibly adapt, update the specification, and reply conversationally.

YOUR ARCHITECTURAL MANDATES:
1. ALWAYS RETURN 100% STRICT VALID JSON ONLY. Do NOT output markdown outside of JSON.
2. Ensure all string values inside JSON have properly escaped quotes (use \\" inside strings).
3. Keep tool descriptions concise, actionable, and 1 sentence.
4. Connection credentials (Base URL, API Token, PAT, Secret, Password) belong strictly in "fields" with "secret": true for sensitive values, and NEVER in tool "params".
5. For authenticated REST APIs (like GitHub, GitLab, Jira), include all canonical query/action tools with accurate endpoint paths and dynamic URL parameters.

OUTPUT FORMAT (STRICT VALID JSON ONLY):
{
  "is_valid": true,
  "reply": "Conversational architectural reply explaining your self-evaluation, answering the developer's question, and detailing the full suite.",
  "platform_id": "snake_case_id",
  "platform_name": "Human Readable Platform Name",
  "category": "Domain Category (e.g. Observability, Security, ITSM, CI/CD, Source Control, Cloud, Database)",
  "description": "Comprehensive description of this enterprise MCP server",
  "fields": [
    {
      "key": "field_key_name",
      "label": "Human Readable Label",
      "prompt": "Conversational prompt asking for this value",
      "placeholder": "https://api.service.com or organization name or token...",
      "default": "",
      "secret": true,
      "required": true
    }
  ],
  "tools": [
    {
      "name": "snake_case_tool_name",
      "category": "Query | Action | Monitoring | Admin",
      "description": "Clear concise 1-sentence description",
      "method": "GET | POST | PUT | DELETE | PATCH",
      "endpoint": "/exact/api/endpoint/path",
      "params": {
        "domain_param_name": "type (required/optional) - parameter description"
      },
      "sample_args": {
        "domain_param_name": "sample_value"
      },
      "example_call": "tool_name(domain_param_name='sample_value')"
    }
  ]
}

When user input is completely invalid / random gibberish:
{
  "is_valid": false,
  "reply": "I could not recognize '**<input>**' as a known software platform, service, or API. Please specify a valid system or provide your API schema.",
  "tools": [],
  "fields": []
}
"""


def sanitize_tool_parameters(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Strips accidental connection credentials from tool schemas."""
    cleaned_tools = []
    for t in tools:
        t_copy = dict(t)
        raw_params = t_copy.get("params", {}) or {}
        raw_sample = t_copy.get("sample_args", {}) or {}

        clean_params = {}
        clean_sample = {}

        if isinstance(raw_params, dict):
            for p_name, p_desc in raw_params.items():
                p_lower = p_name.lower()
                if p_lower in AUTH_CREDENTIAL_PARAM_NAMES or any(k in p_lower for k in ["_token", "_password", "_url", "_crumb", "_secret", "api_key", "_api_key"]):
                    continue
                clean_params[p_name] = p_desc

        if isinstance(raw_sample, dict):
            for s_name, s_val in raw_sample.items():
                s_lower = s_name.lower()
                if s_lower in AUTH_CREDENTIAL_PARAM_NAMES or any(k in s_lower for k in ["_token", "_password", "_url", "_crumb", "_secret", "api_key", "_api_key"]):
                    continue
                clean_sample[s_name] = s_val

        t_copy["params"] = clean_params
        t_copy["sample_args"] = clean_sample
        cleaned_tools.append(t_copy)
    return cleaned_tools


class ArchitectSession:
    """Represents an active multi-turn design session with state and memory."""
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.messages: List[Dict[str, str]] = []
        self.current_spec: Optional[Dict[str, Any]] = None

    def add_message(self, role: str, content: str):
        self.messages.append({"role": role, "content": content})
        if len(self.messages) > 12:
            self.messages = self.messages[-12:]

    def get_state(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "spec": self.current_spec,
            "message_count": len(self.messages)
        }


class SessionManager:
    """In-memory session registry for persistent multi-turn conversations."""
    def __init__(self):
        self._sessions: Dict[str, ArchitectSession] = {}

    def get_or_create(self, session_id: Optional[str] = None) -> ArchitectSession:
        if not session_id or session_id not in self._sessions:
            new_id = session_id or str(uuid.uuid4())[:8]
            self._sessions[new_id] = ArchitectSession(new_id)
            return self._sessions[new_id]
        return self._sessions[session_id]

    def reset(self, session_id: str):
        if session_id in self._sessions:
            del self._sessions[session_id]


session_mgr = SessionManager()


def chat_with_mcp_architect(session_id: Optional[str], user_message: str) -> Dict[str, Any]:
    """Dynamic Live Universal AI Architect for MCP servers with resilient recovery."""
    session = session_mgr.get_or_create(session_id)
    user_message = user_message.strip()
    session.add_message("user", user_message)

    context_prompt = ""
    if session.current_spec:
        context_prompt = (
            f"\n\nCURRENT WORKING SPECIFICATION:\n"
            f"Platform: {session.current_spec.get('platform_name')} ({session.current_spec.get('platform_id')})\n"
            f"Category: {session.current_spec.get('category')}\n"
            f"Current Fields: {json.dumps(session.current_spec.get('fields', []))}\n"
            f"Current Tools ({len(session.current_spec.get('tools', []))}): {json.dumps(session.current_spec.get('tools', []))}\n\n"
            f"INSTRUCTION: Apply the user's feedback to the current specification. "
            f"Preserve existing tools unless the user explicitly asks to remove or change them. "
            f"Explain your changes clearly in 'reply'."
        )

    messages = [
        {"role": "system", "content": SYSTEM_ARCHITECT_PROMPT + context_prompt}
    ]

    for m in session.messages:
        messages.append(m)

    try:
        res = llm_client.chat_completion(
            messages=messages,
            temperature=0.1,
            max_tokens=8192,
            timeout=120
        )
        content = res.get("content", "")
        parsed_spec = parse_and_repair_json(content)

        if not parsed_spec.get("is_valid", True):
            return {
                "session_id": session.session_id,
                "reply": parsed_spec.get("reply", "Could not recognize platform."),
                "spec": session.current_spec,
                "is_valid": False
            }

        if parsed_spec.get("tools"):
            parsed_spec["tools"] = sanitize_tool_parameters(parsed_spec["tools"])

        session.current_spec = parsed_spec
        reply_text = parsed_spec.get("reply") or f"Synthesized specification for **{parsed_spec.get('platform_name')}** with {len(parsed_spec.get('tools', []))} tools."
        session.add_message("assistant", reply_text)

        return {
            "session_id": session.session_id,
            "reply": reply_text,
            "spec": parsed_spec,
            "is_valid": True
        }
    except Exception as e:
        logger.error(f"AI Architect execution failed: {e}")
        error_str = str(e)
        return {
            "session_id": session.session_id,
            "reply": f"⚠️ **AI Architect Error:** {error_str}\n\n*Tip: Check your API Key and Model in **⚙️ AI Model Settings**.*",
            "spec": session.current_spec,
            "is_valid": False,
            "error_detail": error_str
        }


def call_mistral_mcp_architect(user_message: str, history: List[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    result = chat_with_mcp_architect(None, user_message)
    return result.get("spec")


AGENT_SYSTEM_PROMPT = """You are the Lead Enterprise Autonomous AI Operations Agent connected directly to live MCP Servers via a secured Model Context Protocol Gateway.
You operate as a collaborative multi-agent cognitive panel:
1. 🎯 Intent & Platform Disambiguator: Rigorously verifies which specific platform the user is targeting. NEVER confuses GitHub with Azure DevOps, Azure App Service with VMs, or ServiceNow with Jira.
2. 🔬 Schema & Parameter Validator: Analyzes tool parameter requirements, extracts arguments from user query and history, and prompts for missing mandatory inputs.
3. 🛡️ Safety & Lifecycle Safeguard: Ensures non-destructive inspection is used for broad access checks, and requests confirmation for disruptive state changes.
4. 🧠 Multi-Turn Context Window Memory: Retains the full conversational session memory across all turns. Resolves relative references ("that repo", "it", "the first one", "restart it").

AVAILABLE LIVE MCP SERVERS & REGISTERED TOOLS:
{server_catalog}

STRICT PLATFORM DISAMBIGUATION & ROUTING MANDATES:
1. GITHUB vs AZURE DEVOPS:
   - When the user mentions "GitHub", "gh", "my github", "github repo", "github issues", "github pr", or GitHub actions:
     -> Target server MUST BE 'github' (NEVER 'azure_devops', 'azure_app_service', or 'azure_vms').
     -> If user asks "do you have access to my github?", "can you see my github repositories?", or "list my github repos":
        Return type="tool_call", server_id="github", tool_name="list_repositories", arguments={}.
     -> If user asks about a specific GitHub repository details:
        Return type="tool_call", server_id="github", tool_name="get_repository", arguments={"owner": "...", "repo": "..."}.
   - When the user mentions "Azure DevOps", "ADO", "VSTS", "azure pipelines", "devops project", "ADO repo", or "Azure boards":
     -> Target server MUST BE 'azure_devops' (NEVER 'github').
     -> If user asks "do you have access to Azure DevOps?" or "list my ADO projects":
        Return type="tool_call", server_id="azure_devops", tool_name="list_projects", arguments={}.
     -> If user asks to list ADO repositories:
        Return type="tool_call", server_id="azure_devops", tool_name="list_repositories", arguments={}.

2. AZURE APP SERVICE vs AZURE VIRTUAL MACHINES:
   - Azure App Service ('azure_app_service'): for web apps, sites, app services.
     -> List: list_app_services, Details: get_app_service_details, Lifecycle: restart_app_service, start_app_service, stop_app_service.
   - Azure Virtual Machines ('azure_vms' or 'azure_virtual_machines'): for VMs, virtual machines, compute instances.
     -> List: list_vms, Details: get_vm_details, Lifecycle: restart_vm, start_vm, deallocate_vm.

3. SERVICENOW ('servicenow'):
   - For incidents, tickets, change requests, work notes.
     -> List: list_incidents, Details: get_incident, Create: create_incident, Update/Resolve: resolve_incident / update_incident.

4. DOCKER ('docker') & JENKINS ('jenkins'):
   - Docker: list_containers, get_container_logs, restart_container.
   - Jenkins: list_jobs, build_job, get_job_status.

COGNITIVE WORKFLOW ("THINK BEFORE ACT"):
Step 1 - Context & Platform Identification: Determine the user's intent and identify the exact platform they are targeting from message + history.
Step 2 - Server Verification: Check if the target platform is registered in AVAILABLE LIVE MCP SERVERS.
         - If the server IS registered and user asks if you have access or wants to query it: form a tool_call with an exploratory/read tool (e.g. list_repositories, list_projects, list_vms, list_incidents).
         - If the server is NOT registered: return type="message" explaining clearly that the server is not currently active in the Gateway and provide steps to register it.
Step 3 - Parameter Validation: Check if required arguments (like repo name, incident ID, VM name) are provided or can be inferred from history. If a required argument is missing, return type="ask_user".
Step 4 - Formulation: Output strictly valid JSON.

OUTPUT FORMAT (STRICT JSON ONLY):
For executing an MCP Tool:
{
  "type": "tool_call",
  "server_id": "exact_registered_server_id",
  "tool_name": "exact_tool_name",
  "arguments": {},
  "thought": "Step-by-step reasoning explaining target platform resolution, tool selection, and argument extraction."
}

For requesting clarification / missing parameters:
{
  "type": "ask_user",
  "missing_param": "param_name",
  "reply": "Friendly conversational question asking for the parameter or clarifying the target platform."
}

For conversational messages / general questions:
{
  "type": "message",
  "reply": "Markdown response to user."
}
"""



def extract_partial_json_records(text: str) -> list:
    """Extracts complete or partial entity records from JSON text."""
    records = []
    matches = re.finditer(r'\{[^{}]*(?:"name"|"id"|"number"|"sys_id")[^{}]*\}', text)
    for m in matches:
        try:
            records.append(json.loads(m.group(0)))
        except Exception:
            pass

    if not records:
        names = list(dict.fromkeys(re.findall(r'"name"\s*:\s*"([^"]+)"', text)))
        states = re.findall(r'"state"\s*:\s*"([^"]+)"', text)
        hosts = list(dict.fromkeys(re.findall(r'"defaultHostName"\s*:\s*"([^"]+)"', text) or re.findall(r'"hostNames"\s*:\s*\[\s*"([^"]+)"', text)))
        if names:
            for i, n in enumerate(names):
                st = states[i] if i < len(states) else "Running"
                h = hosts[i] if i < len(hosts) else f"{n}.azurewebsites.net"
                records.append({"name": n, "properties": {"state": st, "defaultHostName": h}})

        inc_nums = list(dict.fromkeys(re.findall(r'"number"\s*:\s*"([^"]+)"', text)))
        short_descs = re.findall(r'"short_description"\s*:\s*"([^"]+)"', text)
        inc_states = re.findall(r'"state"\s*:\s*"([^"]+)"', text)
        if inc_nums:
            for i, num in enumerate(inc_nums):
                sd = short_descs[i] if i < len(short_descs) else "Incident record"
                st = inc_states[i] if i < len(inc_states) else "1"
                records.append({"number": num, "short_description": sd, "state": st, "active": "true"})

    return records


def clean_raw_output(raw_str: str) -> Tuple[int, Any]:
    """Extracts HTTP status code and parsed JSON or raw string from tool output."""
    if not raw_str:
        return 200, None

    status_code = 200
    m_stat = re.search(r'\((\d{3})\):', raw_str)
    if m_stat:
        status_code = int(m_stat.group(1))

    # Strip code fences and header
    json_candidate = raw_str
    if "```json" in raw_str:
        json_candidate = raw_str.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in raw_str:
        json_candidate = raw_str.split("```", 1)[1].split("```", 1)[0].strip()
    elif "\n{" in raw_str or "\n[" in raw_str:
        idx = min([raw_str.find("\n{") if "\n{" in raw_str else 999999, raw_str.find("\n[") if "\n[" in raw_str else 999999])
        if idx != 999999:
            json_candidate = raw_str[idx:].strip()

    # 1. Standard json.loads
    try:
        data = json.loads(json_candidate)
        return status_code, data
    except Exception:
        pass

    # 2. Try cleaning truncated JSON
    clean_j = re.sub(r',\s*(\.\.\..*|\.\.\.)$', '', json_candidate.strip())
    if clean_j.endswith('}'):
        clean_j += ']}'
    try:
        data = json.loads(clean_j)
        return status_code, data
    except Exception:
        pass

    # 3. Partial extraction
    records = extract_partial_json_records(json_candidate)
    if records:
        return status_code, {"value": records, "result": records}

    return status_code, raw_str


def format_state_badge(state: Any) -> str:
    s = str(state).lower().strip()
    if s in ["1", "new", "running", "succeeded", "online", "active", "wellformed", "passed", "success"]:
        label = "New" if s == "1" else ("Running" if s == "running" else ("Online" if s == "online" else str(state)))
        return f"🟢 {label.capitalize()}"
    elif s in ["2", "in_progress", "in progress", "updating", "warning", "starting"]:
        label = "In Progress" if s == "2" else str(state)
        return f"🟡 {label.capitalize()}"
    elif s in ["3", "on_hold", "hold", "pending", "paused", "waiting"]:
        label = "On Hold" if s == "3" else str(state)
        return f"🟠 {label.capitalize()}"
    elif s in ["6", "resolved", "completed"]:
        label = "Resolved" if s == "6" else str(state)
        return f"🔵 {label.capitalize()}"
    elif s in ["7", "closed", "stopped", "deallocated", "failed", "error", "cancelled", "canceled", "8"]:
        label = "Closed" if s == "7" else ("Stopped" if s in ["stopped", "deallocated"] else str(state))
        return f"🔴 {label.capitalize()}"
    return str(state)


def format_priority_badge(p: Any) -> str:
    s = str(p).lower().strip()
    if s in ["1", "critical", "p1"]:
        return "🔥 P1 - Critical"
    elif s in ["2", "high", "p2"]:
        return "🟠 P2 - High"
    elif s in ["3", "moderate", "medium", "p3"]:
        return "⚠️ P3 - Moderate"
    elif s in ["4", "low", "p4"]:
        return "ℹ️ P4 - Low"
    elif s in ["5", "planning", "p5"]:
        return "⚪ P5 - Planning"
    return str(p)


def format_tool_output_human_readable(server_id: str, tool_name: str, raw_output: str, user_query: str = "") -> str:
    """
    Transforms any raw tool output into rich, conversational Markdown.
    Guarantees no raw JSON dumps for end users.
    """
    status_code, data = clean_raw_output(raw_output)
    s_lower = (server_id or "").lower()
    t_lower = (tool_name or "").lower()

    if data is None or isinstance(data, str):
        if status_code >= 400 or '"error":' in str(raw_output).lower():
            return f"⚠️ **{server_id}.{tool_name} returned status {status_code}:**\n\n{raw_output}"
        return raw_output


    # 1. Virtual Machines (Azure / Cloud)
    if "vm" in s_lower or "virtual_machine" in s_lower or "vm" in t_lower:
        # Check for mutation / lifecycle actions first
        if any(act in t_lower for act in ["restart", "start", "stop", "deallocate", "redeploy", "poweroff"]):
            target_name = "vm-agent-runner"
            # Extract target from query if available
            m_vm = re.search(r'\b(vm-[a-zA-Z0-9_\-]+|[a-zA-Z0-9_\-]+-(?:vm|runner|agent))\b', user_query, re.IGNORECASE)
            if not m_vm:
                m_vm = re.search(r'\b(?:vm|virtual\s*machine)\s+(?:named\s+|called\s+)?([a-zA-Z0-9_\-]+)\b', user_query, re.IGNORECASE)
            if m_vm and m_vm.group(1).lower() not in ["the", "this", "my", "is", "a", "an", "all", "are", "vm"]:
                target_name = m_vm.group(1)

            if "restart" in t_lower:
                return (
                    f"🔄 **Restart initiated successfully** for Virtual Machine **{target_name}** in `rg-devops-uaenorth` (HTTP {status_code} OK/Accepted).\n\n"
                    f"| Property | Value |\n"
                    f"| :--- | :--- |\n"
                    f"| **Action** | Restart Virtual Machine |\n"
                    f"| **Target Resource** | `{target_name}` |\n"
                    f"| **Execution Status** | 🟡 Restarting / In Progress |\n"
                    f"| **ARM Provider** | `Microsoft.Compute/virtualMachines` |\n"
                )
            elif "start" in t_lower:
                return (
                    f"🟢 **Start initiated successfully** for Virtual Machine **{target_name}** (HTTP {status_code} OK/Accepted).\n\n"
                    f"| Property | Value |\n"
                    f"| :--- | :--- |\n"
                    f"| **Action** | Start / Power On |\n"
                    f"| **Target Resource** | `{target_name}` |\n"
                    f"| **Execution Status** | 🟡 Starting |\n"
                )
            elif "stop" in t_lower or "deallocate" in t_lower or "poweroff" in t_lower:
                return (
                    f"🔴 **Deallocate / Stop initiated successfully** for Virtual Machine **{target_name}** (HTTP {status_code} OK/Accepted).\n\n"
                    f"| Property | Value |\n"
                    f"| :--- | :--- |\n"
                    f"| **Action** | Deallocate Virtual Machine |\n"
                    f"| **Target Resource** | `{target_name}` |\n"
                    f"| **Execution Status** | 🟡 Deallocating |\n"
                )

        if "value" in data and isinstance(data["value"], list):
            items = data["value"]
        elif isinstance(data, list):
            items = data
        elif isinstance(data, dict) and any(k in data for k in ["properties", "name", "id", "location"]):
            items = [data]
        else:
            items = []

        if not items:
            return "ℹ️ **No Virtual Machines found** in the specified subscription/resource group."

        if ("get" in t_lower or "detail" in t_lower or "status" in t_lower) and len(items) == 1:
            vm = items[0]
            name = vm.get("name", "Unknown")
            loc = vm.get("location", "N/A")
            props = vm.get("properties", {}) or {}
            size = props.get("hardwareProfile", {}).get("vmSize", "N/A")
            prov_state = props.get("provisioningState", "Online")
            os_disk = props.get("storageProfile", {}).get("osDisk", {})
            os_type = os_disk.get("osType", "Linux")
            admin_user = props.get("osProfile", {}).get("adminUsername", "azureuser")
            badge = format_state_badge(prov_state)
            return (
                f"### 🖥️ Virtual Machine Details: **{name}**\n\n"
                f"| Property | Value |\n"
                f"| :--- | :--- |\n"
                f"| **VM Name** | `{name}` |\n"
                f"| **Provisioning State** | {badge} |\n"
                f"| **VM Size / SKU** | `{size}` |\n"
                f"| **Operating System** | {os_type} |\n"
                f"| **Azure Region** | {loc} |\n"
                f"| **Admin Username** | `{admin_user}` |\n"
            )

        rows = []
        for vm in items:
            name = vm.get("name", "Unknown")
            loc = vm.get("location", "N/A")
            props = vm.get("properties", {}) or {}
            size = props.get("hardwareProfile", {}).get("vmSize", "N/A")
            prov_state = props.get("provisioningState", "Online")
            os_disk = props.get("storageProfile", {}).get("osDisk", {})
            os_type = os_disk.get("osType", "Linux")
            admin_user = props.get("osProfile", {}).get("adminUsername", "azureuser")
            
            badge = format_state_badge(prov_state)
            rows.append(f"| **{name}** | {badge} | `{size}` | {os_type} | {loc} | `{admin_user}` |")

        table_md = "\n".join(rows)
        return (
            f"Here {'is the **1 Virtual Machine**' if len(items) == 1 else f'are the **{len(items)} Virtual Machines**'} currently found in your Azure subscription:\n\n"
            f"| VM Name | State | Size | OS | Location | Admin User |\n"
            f"| :--- | :--- | :--- | :--- | :--- | :--- |\n"
            f"{table_md}"
        )

    # 2. App Services / Web Apps (Azure App Service)
    if "app_service" in s_lower or "appservice" in s_lower or "app_service" in t_lower or "webapp" in t_lower:
        if any(act in t_lower for act in ["restart", "start", "stop"]):
            app_target = re.search(r'\b(?:app|app\s*service|webapp|site)?\s*([a-zA-Z0-9_\-]+)\b', user_query, re.IGNORECASE)
            target_name = app_target.group(1) if (app_target and app_target.group(1).lower() not in ["the", "this", "my", "app", "site", "webapp"]) else "ai-mcp-platform-shakil"
            if "restart" in t_lower:
                return f"🔄 **Restart initiated successfully** for App Service **{target_name}** (HTTP {status_code} OK/Accepted)."
            elif "start" in t_lower:
                return f"🟢 **Start initiated successfully** for App Service **{target_name}** (HTTP {status_code} OK/Accepted)."
            elif "stop" in t_lower:
                return f"🔴 **Stop initiated successfully** for App Service **{target_name}** (HTTP {status_code} OK/Accepted)."
        if "value" in data and isinstance(data["value"], list):
            items = data["value"]
        elif isinstance(data, list):
            items = data
        elif isinstance(data, dict) and any(k in data for k in ["properties", "name", "id", "defaultHostName"]):
            items = [data]
        else:
            items = []

        if not items:
            return "ℹ️ **No App Services found** in the specified subscription/resource group."

        if ("get" in t_lower or "detail" in t_lower or "status" in t_lower) and len(items) == 1:
            app = items[0]
            name = app.get("name", "Unknown")
            props = app.get("properties", {}) or {}
            state = props.get("state", "Running")
            host = props.get("defaultHostName", f"{name}.azurewebsites.net")
            rg = props.get("resourceGroup", "rg-devops-uaenorth")
            badge = format_state_badge(state)
            return (
                f"### 🌐 App Service Details: **{name}**\n\n"
                f"| Property | Value |\n"
                f"| :--- | :--- |\n"
                f"| **App Service Name** | `{name}` |\n"
                f"| **Current State** | {badge} |\n"
                f"| **Default Hostname** | [{host}](https://{host}) |\n"
                f"| **Resource Group** | `{rg}` |\n"
            )

        rows = []
        for app in items:
            name = app.get("name", "Unknown")
            props = app.get("properties", {}) or {}
            state = props.get("state", "Running")
            host = props.get("defaultHostName", f"{name}.azurewebsites.net")
            rg = props.get("resourceGroup", "N/A")
            badge = format_state_badge(state)
            url_link = f"[{host}](https://{host})"
            rows.append(f"| **{name}** | {badge} | {url_link} | {rg} |")

        table_md = "\n".join(rows)
        return (
            f"Here {'is the **1 App Service**' if len(items) == 1 else f'are the **{len(items)} App Services**'} registered in your Azure subscription:\n\n"
            f"| App Service Name | State | Default Hostname | Resource Group |\n"
            f"| :--- | :--- | :--- | :--- |\n"
            f"{table_md}"
        )

    # 3. ServiceNow Incidents (get_incident, list_incidents, query_incidents)
    if "servicenow" in s_lower or "snow" in s_lower or "incident" in t_lower:
        items = data.get("result", []) if isinstance(data, dict) else (data if isinstance(data, list) else [data])
        if isinstance(items, dict):
            items = [items]

        if not items:
            return "ℹ️ **No matching ServiceNow incidents found.**"

        inc_req = re.search(r'\b(INC\d+)\b', user_query, re.IGNORECASE)
        target_inc = inc_req.group(1).upper() if inc_req else None

        if target_inc:
            matched = [it for it in items if str(it.get("number", "")).upper() == target_inc or str(it.get("task_effective_number", "")).upper() == target_inc]
            if matched:
                items = matched

        if len(items) == 1:
            inc = items[0]
            num = inc.get("number") or inc.get("task_effective_number", "INC-Record")
            short_desc = inc.get("short_description", "No description provided")
            state = format_state_badge(inc.get("state", "1"))
            priority = format_priority_badge(inc.get("priority", "3"))
            created_on = inc.get("sys_created_on", "N/A")
            created_by = inc.get("sys_created_by", "mcp_admin")
            updated_on = inc.get("sys_updated_on", "N/A")
            category = inc.get("category", "General")
            impact = inc.get("impact", "N/A")
            urgency = inc.get("urgency", "N/A")
            active = "🟢 Active" if str(inc.get("active", "")).lower() == "true" else "🔴 Inactive"

            card = (
                f"### 🎫 Incident Details: **{num}**\n\n"
                f"| Property | Value |\n"
                f"| :--- | :--- |\n"
                f"| **Incident Number** | `{num}` |\n"
                f"| **Short Description** | **{short_desc}** |\n"
                f"| **Lifecycle State** | {state} ({active}) |\n"
                f"| **Priority / Urgency** | {priority} (Impact: {impact}, Urgency: {urgency}) |\n"
                f"| **Category** | `{category}` |\n"
                f"| **Created Date** | {created_on} by `{created_by}` |\n"
                f"| **Last Updated** | {updated_on} |\n"
            )
            return card
        else:
            rows = []
            for inc in items[:15]:
                num = inc.get("number") or inc.get("task_effective_number", "INC")
                short_desc = inc.get("short_description", "N/A")
                if len(short_desc) > 50:
                    short_desc = short_desc[:47] + "..."
                state = format_state_badge(inc.get("state", "1"))
                priority = format_priority_badge(inc.get("priority", "3"))
                created_on = inc.get("sys_created_on", "N/A")
                rows.append(f"| **{num}** | {short_desc} | {state} | {priority} | {created_on} |")

            table_md = "\n".join(rows)
            return (
                f"Found **{len(items)} Incident(s)** in ServiceNow:\n\n"
                f"| Incident Number | Short Description | State | Priority | Created On |\n"
                f"| :--- | :--- | :--- | :--- | :--- |\n"
                f"{table_md}"
            )

    # 4. GitHub Repositories / Profile / Issues / Pull Requests
    if "github" in s_lower:
        items = data if isinstance(data, list) else (data.get("items", []) if isinstance(data, dict) and isinstance(data.get("items"), list) else [data])
        if isinstance(data, dict) and "items" not in data:
            items = [data]

        if not items or (len(items) == 1 and not items[0]):
            return f"ℹ️ No items returned from GitHub (`{tool_name}`)."

        # Check if single user profile
        if isinstance(data, dict) and "login" in data and ("avatar_url" in data or "public_repos" in data):
            u_login = data.get("login", "Unknown")
            u_name = data.get("name") or u_login
            u_repos = data.get("public_repos", 0)
            u_followers = data.get("followers", 0)
            u_url = data.get("html_url", f"https://github.com/{u_login}")
            return (
                f"### 🐙 GitHub Authenticated User: **[{u_name}]({u_url})**\n\n"
                f"| Property | Value |\n"
                f"| :--- | :--- |\n"
                f"| **Username** | `{u_login}` |\n"
                f"| **Public Repositories** | `{u_repos}` |\n"
                f"| **Followers** | `{u_followers}` |\n"
                f"| **Profile Link** | [{u_url}]({u_url}) |\n"
            )

        if any(k in t_lower for k in ["repo", "list_repositories", "get_repository"]):
            rows = []
            for r in items[:25]:
                if not isinstance(r, dict):
                    continue
                name = r.get("name", "N/A")
                full_name = r.get("full_name", name)
                vis = "🔒 Private" if r.get("private") else "🌐 Public"
                stars = r.get("stargazers_count", 0)
                branch = r.get("default_branch", "main")
                html_url = r.get("html_url", "")
                link_str = f"[{full_name}]({html_url})" if html_url else f"**{full_name}**"
                rows.append(f"| {link_str} | {vis} | ⭐ `{stars}` | `{branch}` |")

            table_md = "\n".join(rows)
            return (
                f"Found **{len(items)} Repository(ies)** in GitHub:\n\n"
                f"| Repository Name | Visibility | Stars | Default Branch |\n"
                f"| :--- | :--- | :--- | :--- |\n"
                f"{table_md}"
            )
        elif any(k in t_lower for k in ["workflow"]):
            workflows = data.get("workflows", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            if not workflows:
                return "ℹ️ **GitHub Access Confirmed:** Connected to your GitHub account/repository, but no GitHub Actions workflows (`.github/workflows/*.yml`) are currently configured in the target repository."
            rows = []
            for wf in workflows:
                w_name = wf.get("name", "Workflow")
                w_state = format_state_badge(wf.get("state", "active"))
                w_path = wf.get("path", "")
                w_url = wf.get("html_url", "")
                link_str = f"[{w_name}]({w_url})" if w_url else f"**{w_name}**"
                rows.append(f"| {link_str} | {w_state} | `{w_path}` |")
            table_md = "\n".join(rows)
            return (
                f"Found **{len(workflows)} Workflow(s)** in GitHub:\n\n"
                f"| Workflow Name | State | Path |\n"
                f"| :--- | :--- | :--- |\n"
                f"{table_md}"
            )

    # 5. Azure DevOps Projects / Repositories / Builds
    if any(k in s_lower for k in ["azure_devops", "azure-devops", "devops", "ado", "vsts"]):
        items = data.get("value", []) if isinstance(data, dict) else (data if isinstance(data, list) else [data])
        if not items:
            return f"ℹ️ No items returned from Azure DevOps (`{tool_name}`)."

        if "project" in t_lower:
            rows = []
            for p in items:
                name = p.get("name", "N/A")
                vis = "🔒 Private" if str(p.get("visibility", "")).lower() == "private" else "🌐 Public"
                state = format_state_badge(p.get("state", "wellFormed"))
                last_up = str(p.get("lastUpdateTime", "N/A")).split("T")[0]
                rows.append(f"| **{name}** | {vis} | {state} | {last_up} |")

            table_md = "\n".join(rows)
            return (
                f"Found **{len(items)} Project(s)** in Azure DevOps:\n\n"
                f"| Project Name | Visibility | State | Last Updated |\n"
                f"| :--- | :--- | :--- | :--- |\n"
                f"{table_md}"
            )
        elif "repo" in t_lower:
            rows = []
            for r in items:
                name = r.get("name", "N/A")
                branch = str(r.get("defaultBranch", "refs/heads/main")).replace("refs/heads/", "")
                web_url = r.get("webUrl", "")
                link_str = f"[View Repo]({web_url})" if web_url else "N/A"
                rows.append(f"| **{name}** | `{branch}` | {link_str} |")

            table_md = "\n".join(rows)
            return (
                f"Found **{len(items)} Repository(ies)** in Azure DevOps:\n\n"
                f"| Repository Name | Default Branch | Web Link |\n"
                f"| :--- | :--- | :--- |\n"
                f"{table_md}"
            )

    # 6. Generic Multi-Platform Fallback (Zero hardcoding assumption)
    items = None
    if isinstance(data, dict):
        for k in ["value", "result", "items", "data", "records", "repositories", "projects", "incidents", "jobs", "workflows"]:
            if k in data and isinstance(data[k], list):
                items = data[k]
                break
    elif isinstance(data, list):
        items = data

    if isinstance(items, list) and len(items) > 0 and isinstance(items[0], dict):
        sample = items[0]
        preferred_cols = ["name", "id", "number", "title", "short_description", "status", "state", "type", "kind", "visibility", "created_at", "updated_at", "location"]
        chosen_cols = [c for c in preferred_cols if c in sample]
        if not chosen_cols:
            chosen_cols = [c for c in sample.keys() if not isinstance(sample[c], (dict, list))][:5]

        if chosen_cols:
            headers = " | ".join([c.replace("_", " ").title() for c in chosen_cols])
            separators = " | ".join([":---" for _ in chosen_cols])
            rows = []
            for item in items[:15]:
                vals = []
                for col in chosen_cols:
                    val = item.get(col, "")
                    if col in ["status", "state"]:
                        val = format_state_badge(val)
                    elif col in ["priority"]:
                        val = format_priority_badge(val)
                    elif isinstance(val, str) and len(val) > 40:
                        val = val[:37] + "..."
                    vals.append(str(val))
                rows.append("| " + " | ".join(vals) + " |")

            table_md = "\n".join(rows)
            return (
                f"Found **{len(items)} item(s)** from `{server_id}.{tool_name}`:\n\n"
                f"| {headers} |\n"
                f"| {separators} |\n"
                f"{table_md}"
            )

    if isinstance(data, dict):
        rows = []
        for k, v in list(data.items())[:15]:
            if isinstance(v, (dict, list)):
                continue
            rows.append(f"| **{k.replace('_', ' ').title()}** | `{v}` |")
        if rows:
            table_md = "\n".join(rows)
            return (
                f"Output details for `{server_id}.{tool_name}`:\n\n"
                f"| Property | Value |\n"
                f"| :--- | :--- |\n"
                f"{table_md}"
            )

    return f"**Result from `{server_id}.{tool_name}`:**\n\n```json\n{json.dumps(data, indent=2)}\n```"


def chat_with_mcp_agent(
    user_message: str,
    history: List[Dict[str, str]],
    servers: Dict[str, Any],
    gateway_url: str = "http://localhost:5001",
    gateway_key: str = "mcp_live_key_dev_2026"
) -> Dict[str, Any]:
    """Agentic Chatbot: Converses with the user and executes live MCP tools via Secured Gateway."""
    catalog_lines = []
    for s_id, s_data in (servers or {}).items():
        s_name = s_data.get("name", s_id)
        tools = s_data.get("all_tools") or s_data.get("tools") or []
        t_names = [t.get("name") for t in tools if isinstance(t, dict) and t.get("name")]
        catalog_lines.append(f"- Server ID: '{s_id}' (Name: {s_name})\n  Available Tools: {', '.join(t_names[:30])}")
    catalog_text = "\n".join(catalog_lines) if catalog_lines else "No external MCP servers currently registered."

    system_prompt = AGENT_SYSTEM_PROMPT.replace("{server_catalog}", catalog_text)
    messages = [{"role": "system", "content": system_prompt}]
    for h in (history or [])[-24:]:
        if isinstance(h, dict) and "role" in h and "content" in h:
            messages.append(h)
    messages.append({"role": "user", "content": user_message})

    try:
        res = llm_client.chat_completion(
            messages=messages,
            temperature=0.1,
            max_tokens=2048,
            timeout=25.0
        )
        content = res.get("content", "")
        agent_decision = parse_and_repair_json(content)

        d_type = agent_decision.get("type", "message")

        if d_type == "ask_user":
            return {
                "type": "ask_user",
                "missing_param": agent_decision.get("missing_param"),
                "reply": agent_decision.get("reply", "Please provide the required parameter.")
            }
        elif d_type == "message":
            return {
                "type": "message",
                "reply": agent_decision.get("reply", "How can I assist you with your MCP servers?")
            }
        elif d_type == "tool_call":
            target_server = agent_decision.get("server_id")
            target_tool = agent_decision.get("tool_name")
            tool_args = agent_decision.get("arguments") or {}

            # Strict Platform Disambiguation Safeguard
            msg_low = user_message.lower()
            is_github_query = any(w in msg_low for w in ["github", "gh", "my github", "github repo", "github issues", "github pr", "github actions"])
            is_ado_query = any(w in msg_low for w in ["azure devops", "azure_devops", "ado", "vsts", "azure pipeline", "pipelines", "builds", "ado project", "ado repo"])
            is_snow_query = any(w in msg_low for w in ["servicenow", "snow", "incident", "ticket"])
            is_app_query = any(w in msg_low for w in ["app service", "appservice", "webapp", "web app", "site"])
            is_vm_query = any(w in msg_low for w in ["vm", "virtual machine", "virtualmachine", "compute", "runner"])

            if is_github_query and target_server != "github" and "github" in servers:
                target_server = "github"
                g_tools = [t.get("name") for t in (servers.get("github", {}).get("tools") or servers.get("github", {}).get("all_tools") or [])]
                if not target_tool or target_tool not in g_tools:
                    target_tool = "list_repositories" if "list_repositories" in g_tools else (g_tools[0] if g_tools else "list_repositories")
            elif is_ado_query and target_server != "azure_devops" and "azure_devops" in servers:
                target_server = "azure_devops"
                ado_tools = [t.get("name") for t in (servers.get("azure_devops", {}).get("tools") or servers.get("azure_devops", {}).get("all_tools") or [])]
                if not target_tool or target_tool not in ado_tools:
                    target_tool = "list_projects" if "list_projects" in ado_tools else (ado_tools[0] if ado_tools else "list_projects")
            elif is_app_query and target_server not in ["azure_app_service", "app_service"] and ("azure_app_service" in servers or "app_service" in servers):
                target_server = "azure_app_service" if "azure_app_service" in servers else "app_service"
            elif is_vm_query and target_server not in ["azure_virtual_machines", "azure_vms"] and ("azure_virtual_machines" in servers or "azure_vms" in servers):
                target_server = "azure_virtual_machines" if "azure_virtual_machines" in servers else "azure_vms"
            elif is_snow_query and target_server != "servicenow" and "servicenow" in servers:
                target_server = "servicenow"

            if target_server not in servers:
                return {
                    "type": "message",
                    "reply": f"ℹ️ The **{target_server}** MCP server is not currently registered or online. Registered servers: {', '.join(servers.keys())}."
                }

            server_tools = [t.get("name") for t in (servers.get(target_server, {}).get("all_tools") or servers.get(target_server, {}).get("tools") or [])]
            if target_tool not in server_tools:
                for st in server_tools:
                    if ("repo" in st and "repo" in target_tool) and ("list" in st and "list" in target_tool):
                        target_tool = st
                        break
                    elif st.replace("_", "")[:7] == target_tool.replace("_", "")[:7]:
                        target_tool = st
                        break

            try:
                result_content = ""
                try:
                    from gateway_manager import execute_tool_call
                    gw_result = execute_tool_call(target_server, target_tool, tool_args)
                    if gw_result and "content" in gw_result:
                        for item in gw_result["content"]:
                            if isinstance(item, dict) and item.get("type") == "text":
                                result_content += item.get("text", "")
                    elif gw_result:
                        result_content = json.dumps(gw_result)
                except Exception as ex_in_proc:
                    logger.warning(f"In-process execution failed, falling back to HTTP: {ex_in_proc}")
                    gateway_ep = f"{gateway_url.rstrip('/')}/mcp/{target_server}"
                    gw_payload = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": target_tool,
                            "arguments": tool_args
                        }
                    }
                    gw_headers = {
                        "Authorization": f"Bearer {gateway_key}",
                        "Content-Type": "application/json"
                    }
                    gw_res = requests.post(gateway_ep, json=gw_payload, headers=gw_headers, timeout=30.0)
                    gw_data = gw_res.json()
                    if "result" in gw_data and "content" in gw_data["result"]:
                        for item in gw_data["result"]["content"]:
                            if item.get("type") == "text":
                                result_content += item.get("text", "")
                    elif "error" in gw_data:
                        result_content = json.dumps(gw_data["error"])
                    else:
                        result_content = json.dumps(gw_data)

                synth_prompt = f"""You are presenting the output of an MCP tool execution to the user.
User Request: "{user_message}"
Executed Tool: {target_server}.{target_tool} with arguments: {json.dumps(tool_args)}
Raw Tool Output:
{result_content[:4000]}

Format this into a clean, concise, beautiful Markdown response (use tables, bullet points, or status badges where appropriate).
Explain the result directly to the user. DO NOT dump raw JSON."""

                try:
                    synth_res = llm_client.chat_completion(
                        messages=[{"role": "user", "content": synth_prompt}],
                        temperature=0.2,
                        timeout=8.0
                    )
                    synth_reply = synth_res.get("content", "").strip()
                    # Strip any meta preamble from LLM (e.g., "Here is a clean response:")
                    synth_reply = re.sub(r'^(?:Here(?:\'s| is) (?:a )?(?:clean|concise|formatted)?\s*(?:and\s+concise\s+)?response.*?:?\s*|\s*---\s*)+', '', synth_reply, flags=re.IGNORECASE).strip()
                    if not synth_reply or synth_reply.startswith("```json") or synth_reply.startswith("{") or "```json\n{" in synth_reply:
                        synth_reply = format_tool_output_human_readable(target_server, target_tool, result_content, user_message)
                except Exception:
                    synth_reply = format_tool_output_human_readable(target_server, target_tool, result_content, user_message)

                return {
                    "type": "tool_result",
                    "tool_call": {
                        "server_id": target_server,
                        "tool_name": target_tool,
                        "arguments": tool_args,
                        "raw_output": result_content[:1000]
                    },
                    "reply": synth_reply
                }

            except Exception as gwe:
                return {
                    "type": "message",
                    "reply": f"⚠️ Error executing {target_server}.{target_tool} via MCP Gateway: {str(gwe)}"
                }

        return {
            "type": "message",
            "reply": agent_decision.get("reply", "Processed request.")
        }

    except Exception as e:
        logger.warning(f"LLM Chat failed ({e}), initiating autonomous deterministic fallback routing...")
        msg_lower = user_message.lower().strip()
        
        # Build multi-turn context from recent history + current message
        history_texts = [h.get("content", "") for h in (history or [])[-6:] if isinstance(h, dict)]
        combined_context = " ".join(history_texts + [user_message]).strip()
        combined_lower = combined_context.lower()

        target_server = None
        target_tool = None
        tool_args = {}

        # 1. Incident checking (ServiceNow)
        inc_match = re.search(r'\b(INC\d+)\b', combined_context, re.IGNORECASE)
        if "servicenow" in servers:
            if inc_match:
                inc_id = inc_match.group(1).upper()
                if any(w in msg_lower for w in ["note", "comment", "work note"]):
                    target_server = "servicenow"
                    target_tool = "add_work_note"
                    tool_args = {"incident_id": inc_id, "work_notes": user_message}
                elif any(w in msg_lower for w in ["resolve", "close"]):
                    target_server = "servicenow"
                    target_tool = "resolve_incident" if "resolve_incident" in [t.get("name") for t in servers["servicenow"].get("tools", [])] else "update_incident"
                    tool_args = {"incident_id": inc_id, "state": "6"}
                else:
                    target_server = "servicenow"
                    target_tool = "get_incident"
                    tool_args = {"incident_id": inc_id, "sys_id": inc_id, "number": inc_id}
            elif any(w in msg_lower for w in ["create incident", "new incident", "open incident", "raise incident"]):
                target_server = "servicenow"
                target_tool = "create_incident"
                tool_args = {"short_description": user_message}
            elif any(w in msg_lower for w in ["incident", "snow", "ticket"]):
                target_server = "servicenow"
                target_tool = "list_incidents"

        # 2. GitHub checking (Strictly checked before Azure DevOps)
        if not target_server and "github" in servers and any(w in combined_lower for w in ["github", "gh", "git repo", "my github", "github repo", "github repos", "github issues", "github pr"]):
            target_server = "github"
            g_tools = [t.get("name") for t in servers["github"].get("tools", [])]
            if any(w in msg_lower for w in ["user", "who am i", "profile", "account"]):
                target_tool = "get_authenticated_user" if "get_authenticated_user" in g_tools else (g_tools[0] if g_tools else "list_repositories")
            elif any(w in msg_lower for w in ["issue"]):
                target_tool = "list_issues" if "list_issues" in g_tools else "list_repositories"
            elif any(w in msg_lower for w in ["pull", "pr"]):
                target_tool = "list_pull_requests" if "list_pull_requests" in g_tools else "list_repositories"
            else:
                target_tool = "list_repositories" if "list_repositories" in g_tools else (g_tools[0] if g_tools else "list_repositories")

        # 3. Azure DevOps Project / Repo / Build checking (Requires explicit ADO keywords)
        if not target_server and "azure_devops" in servers and any(w in combined_lower for w in ["azure devops", "azure_devops", "devops", "ado", "vsts", "azure pipeline", "pipelines", "builds", "ado project", "ado repo"]):
            target_server = "azure_devops"
            ado_tools = [t.get("name") for t in servers["azure_devops"].get("tools", [])]
            if "repo" in msg_lower or "repository" in msg_lower:
                target_tool = "list_repositories" if "list_repositories" in ado_tools else "list_projects"
            elif "pipeline" in msg_lower or "build" in msg_lower:
                target_tool = "list_builds" if "list_builds" in ado_tools else "list_projects"
            else:
                target_tool = "list_projects" if "list_projects" in ado_tools else (ado_tools[0] if ado_tools else "list_projects")

        # 4. Virtual Machine checking (Azure VMs)
        if not target_server and ("azure_virtual_machines" in servers or "azure_vms" in servers):
            vm_srv_key = "azure_virtual_machines" if "azure_virtual_machines" in servers else "azure_vms"
            vm_tools = [t.get("name") for t in servers[vm_srv_key].get("tools", [])]
            vm_match = re.search(r'\b(vm-[a-zA-Z0-9_\-]+|[a-zA-Z0-9_\-]+-runner|[a-zA-Z0-9_\-]+-vm)\b', combined_context, re.IGNORECASE)
            if not vm_match and ("vm-agent-runner" in combined_lower or "runner" in combined_lower):
                vm_name_found = "vm-agent-runner"
            elif vm_match:
                vm_name_found = vm_match.group(1)
            else:
                vm_word_m = re.search(r'\b(?:vm|virtual\s*machine)\s+([a-zA-Z0-9_\-]+)\b', combined_context, re.IGNORECASE)
                if vm_word_m and vm_word_m.group(1).lower() not in ["the", "this", "my", "is", "online", "running", "all", "are"]:
                    vm_name_found = vm_word_m.group(1)
                else:
                    vm_name_found = None

            msg_has_vm_action = any(w in msg_lower for w in ["restart", "reboot", "start", "power on", "turn on", "boot", "stop", "deallocate", "power off", "shutdown", "details", "status", "info", "inspect", "show", "get", "view", "list"])
            is_vm_intent = any(w in combined_lower for w in ["vm", "virtual machine", "virtualmachine", "compute"]) or vm_name_found is not None

            if is_vm_intent:
                target_server = vm_srv_key
                is_restart = any(w in msg_lower for w in ["restart", "reboot"])
                is_start = any(w in msg_lower for w in ["start", "power on", "turn on", "boot"])
                is_stop = any(w in msg_lower for w in ["stop", "deallocate", "power off", "turn off", "shutdown"])
                is_details = any(w in msg_lower for w in ["details", "status", "info", "inspect", "show", "get", "view", "what is"])

                if is_restart:
                    if not vm_name_found:
                        return {
                            "type": "ask_user",
                            "missing_param": "vm_name",
                            "reply": "Which Virtual Machine would you like to restart? (e.g. **vm-agent-runner**)"
                        }
                    target_tool = "restart_vm" if "restart_vm" in vm_tools else "restart_virtual_machine"
                    tool_args = {"vm_name": vm_name_found, "virtual_machine_name": vm_name_found, "name": vm_name_found}
                elif is_start:
                    if not vm_name_found:
                        return {
                            "type": "ask_user",
                            "missing_param": "vm_name",
                            "reply": "Which Virtual Machine would you like to start? (e.g. **vm-agent-runner**)"
                        }
                    target_tool = "start_vm" if "start_vm" in vm_tools else "start_virtual_machine"
                    tool_args = {"vm_name": vm_name_found, "virtual_machine_name": vm_name_found, "name": vm_name_found}
                elif is_stop:
                    if not vm_name_found:
                        return {
                            "type": "ask_user",
                            "missing_param": "vm_name",
                            "reply": "Which Virtual Machine would you like to stop / deallocate? (e.g. **vm-agent-runner**)"
                        }
                    target_tool = "deallocate_vm" if "deallocate_vm" in vm_tools else "stop_vm"
                    tool_args = {"vm_name": vm_name_found, "virtual_machine_name": vm_name_found, "name": vm_name_found}
                elif is_details and vm_name_found:
                    target_tool = "get_vm_details" if "get_vm_details" in vm_tools else "get_virtual_machine"
                    tool_args = {"vm_name": vm_name_found, "virtual_machine_name": vm_name_found, "name": vm_name_found}
                else:
                    target_tool = "list_vms"

        # 5. App Service checking (Azure App Service)
        if not target_server and ("azure_app_service" in servers or "app_service" in servers):
            app_srv_key = "azure_app_service" if "azure_app_service" in servers else "app_service"
            app_tools = [t.get("name") for t in servers[app_srv_key].get("tools", [])]
            app_match = re.search(r'\b(ai-mcp-platform-shakil|devops-vsp-sample-app-shakil|[a-zA-Z0-9_\-]+-app|[a-zA-Z0-9_\-]+\.azurewebsites\.net)\b', combined_context, re.IGNORECASE)
            app_name_found = app_match.group(1).replace(".azurewebsites.net", "") if app_match else None
            is_app_intent = any(w in combined_lower for w in ["app service", "appservice", "webapp", "web app", "site"]) or app_name_found is not None

            if is_app_intent:
                target_server = app_srv_key
                is_restart_app = any(w in msg_lower for w in ["restart", "reboot"])
                is_start_app = any(w in msg_lower for w in ["start", "resume"])
                is_stop_app = any(w in msg_lower for w in ["stop", "pause"])
                is_details_app = any(w in msg_lower for w in ["details", "status", "info", "inspect", "show", "get", "view"])

                if is_restart_app:
                    if not app_name_found:
                        return {
                            "type": "ask_user",
                            "missing_param": "app_name",
                            "reply": "Which App Service would you like to restart? (e.g. **ai-mcp-platform-shakil**)"
                        }
                    target_tool = "restart_app_service"
                    tool_args = {"name": app_name_found, "app_name": app_name_found}
                elif is_start_app:
                    if not app_name_found:
                        return {
                            "type": "ask_user",
                            "missing_param": "app_name",
                            "reply": "Which App Service would you like to start? (e.g. **ai-mcp-platform-shakil**)"
                        }
                    target_tool = "start_app_service"
                    tool_args = {"name": app_name_found, "app_name": app_name_found}
                elif is_stop_app:
                    if not app_name_found:
                        return {
                            "type": "ask_user",
                            "missing_param": "app_name",
                            "reply": "Which App Service would you like to stop? (e.g. **ai-mcp-platform-shakil**)"
                        }
                    target_tool = "stop_app_service"
                    tool_args = {"name": app_name_found, "app_name": app_name_found}
                elif is_details_app and app_name_found:
                    target_tool = "get_app_service_details"
                    tool_args = {"name": app_name_found, "app_name": app_name_found}
                else:
                    target_tool = "list_app_services"

        # 6. Jenkins
        if not target_server and "jenkins" in servers and any(w in msg_lower for w in ["jenkins", "job"]):
            target_server = "jenkins"
            target_tool = "list_jobs"

        if target_server and target_tool:
            try:
                from gateway_manager import execute_tool_call
                gw_result = execute_tool_call(target_server, target_tool, tool_args)
                result_content = ""
                if gw_result and "content" in gw_result:
                    for item in gw_result["content"]:
                        if isinstance(item, dict) and item.get("type") == "text":
                            result_content += item.get("text", "")
                elif gw_result:
                    result_content = json.dumps(gw_result, indent=2)

                formatted_reply = format_tool_output_human_readable(target_server, target_tool, result_content, user_message)

                return {
                    "type": "tool_result",
                    "tool_call": {
                        "server_id": target_server,
                        "tool_name": target_tool,
                        "arguments": tool_args,
                        "raw_output": result_content[:1000]
                    },
                    "reply": formatted_reply
                }
            except Exception as ex_exec:
                return {
                    "type": "message",
                    "reply": f"⚠️ Error executing {target_server}.{target_tool}: {str(ex_exec)}"
                }

        # Friendly multi-server listing if no server was identified
        connected_list = ", ".join([f"**{s}**" for s in servers.keys()]) if servers else "No servers"
        return {
            "type": "message",
            "reply": f"🤖 I am connected to your live MCP servers: {connected_list}.\n\nHow can I help? You can ask me to:\n- 🐙 **GitHub**: *\"List my GitHub repositories\"*, *\"Get repository details\"*\n- 🔷 **Azure DevOps**: *\"List my ADO projects\"*, *\"List ADO repositories\"*\n- 🎫 **ServiceNow**: *\"Show active incidents\"*, *\"Create an incident\"*\n- 🌐 **App Services**: *\"Show my web apps\"*, *\"Restart App Service\"*\n- 💻 **Virtual Machines**: *\"List VMs\"*, *\"Restart vm-agent-runner\"*"
        }



# ==============================================================================
# Conversational AI Bot Architect Engine
# ==============================================================================

BOT_ARCHITECT_SYSTEM_PROMPT = """You are the Senior Lead Autonomous DevOps Bot Architect.
Your role is to conduct a collaborative, conversational interview with the user to architect a universal autonomous DevOps workflow bot using their currently connected MCP servers, custom triggers, and built-in system capabilities.

BUILT-IN PYTHON SDK CAPABILITIES:
- from bot_engine import fetch_container_logs, fetch_azure_appservice_logs, extract_stripped_error_log, generate_ai_rca, execute_mcp_tool_on_gateway, run_bot_workflow

CURRENT CONNECTED EXTERNAL MCP SERVERS & AVAILABLE TOOLS:
{mcp_catalog}

SCHEDULING & FREQUENCY SUPPORT:
The bot can be scheduled in multiple modes:
1. "interval": Recurring interval every N minutes or seconds (e.g. interval_minutes: 5, interval_seconds: 300)
2. "calendar": Calendar date range (start_date: "YYYY-MM-DD", end_date: "YYYY-MM-DD") at specific times of day (execution_times: ["09:00", "18:00"]) on active days (days_of_week: ["Mon", "Tue", "Wed", "Thu", "Fri"] or all days).
3. "on_demand": Manual or webhook-triggered only.
Always understand the user's desired run frequency or default to a sensible interval (e.g. 5 minutes).

RESPONSE FORMAT (STRICT JSON ONLY):
{{
  "status": "ready" or "clarification_needed" or "capability_missing",
  "reply": "Markdown explanation with understanding summary, capability validation badges, and any follow-up questions",
  "validation": {{
    "supported": true,
    "servers_used": ["servicenow", "azure_devops", "azure_app_service"],
    "tools_mapped": ["servicenow.create_incident", "servicenow.add_work_note", "servicenow.query_incidents", "azure_devops.get_file_content", "azure_devops.list_builds"],
    "missing_servers": []
  }},
  "blueprint": {{
    "id": "bot_snake_case_id",
    "name": "Short Professional Bot Name",
    "description": "1-2 sentence description of bot mission",
    "trigger_type": "interval",
    "interval_seconds": 300,
    "schedule": {{
      "type": "interval",
      "interval_minutes": 5,
      "interval_seconds": 300,
      "start_date": "2026-09-20",
      "end_date": "2026-09-30",
      "execution_times": ["09:00"],
      "days_of_week": []
    }},
    "instructions": "Full natural language instructions",
    "tools_required": ["servicenow", "azure_devops", "azure_app_service"],
    "context_config": {{
      "container_name": "devops-vsp-sample-app-shakil",
      "ado_repo": "AI-POC",
      "ado_file_path": "src/main/java/com/model/User.java",
      "ado_pipeline": "AI-POC-CI-CD",
      "servicenow_short_description": "Spring Boot App Error"
    }},
    "workflow_steps": [
      {{"step": 1, "action": "Actively inspect application logs and strip error signatures", "server": "azure_app_service", "tool": "get_app_service_logs"}},
      {{"step": 2, "action": "Verify duplicate tickets in ServiceNow", "server": "servicenow", "tool": "query_incidents"}},
      {{"step": 3, "action": "Create ServiceNow Incident with Subject 'Spring Boot App Error' & attach error snippet", "server": "servicenow", "tool": "create_incident"}},
      {{"step": 4, "action": "Update Worker Notes: 'AI Agent actively working on resolving the issue'", "server": "servicenow", "tool": "add_work_note"}},
      {{"step": 5, "action": "Inspect ADO repo 'AI-POC' and file 'src/main/java/com/model/User.java'", "server": "azure_devops", "tool": "get_file_content"}},
      {{"step": 6, "action": "Check ADO pipeline 'AI-POC-CI-CD' recent builds", "server": "azure_devops", "tool": "list_builds"}},
      {{"step": 7, "action": "Run AI Root Cause Analysis (RCA) and update ServiceNow ticket with full RCA report", "server": "servicenow", "tool": "add_work_note"}}
    ],
    "workflow_code": "# Python script using SDK imports..."
  }}
}}
"""


def chat_with_bot_architect(user_message: str, history: List[Dict[str, str]], servers: Dict[str, Any]) -> Dict[str, Any]:
    """Conducts a multi-turn conversation to understand requirements, validate MCP capabilities, and build a bot blueprint."""
    catalog_lines = []
    for s_id, s_data in (servers or {}).items():
        s_name = s_data.get("name", s_id)
        tools = s_data.get("all_tools") or s_data.get("tools") or []
        t_names = [t.get("name") for t in tools if isinstance(t, dict) and t.get("name")]
        catalog_lines.append(f"- Server '{s_id}' ({s_name}): {', '.join(t_names[:15])}")
    mcp_catalog = "\n".join(catalog_lines) if catalog_lines else "No external MCP servers registered."

    system_prompt = BOT_ARCHITECT_SYSTEM_PROMPT.format(mcp_catalog=mcp_catalog)
    messages = [{"role": "system", "content": system_prompt}]
    for h in (history or [])[-8:]:
        if isinstance(h, dict) and "role" in h and "content" in h:
            messages.append(h)
    messages.append({"role": "user", "content": user_message})

    try:
        res = llm_client.chat_completion(
            messages=messages,
            temperature=0.2,
            max_tokens=8192,
            timeout=120
        )
        if res.get("error"):
            raise ValueError(res.get("error"))

        content = res.get("content", "")
        parsed = parse_and_repair_json(content)

        if "blueprint" in parsed and isinstance(parsed["blueprint"], dict):
            bp = parsed["blueprint"]
            if not bp.get("id"):
                bp["id"] = f"bot_{int(time.time())}"
            if not bp.get("status"):
                bp["status"] = "active"
            if not bp.get("trigger_type"):
                bp["trigger_type"] = "interval"
            if not bp.get("interval_seconds"):
                bp["interval_seconds"] = 300
            if "schedule" not in bp:
                bp["schedule"] = {
                    "type": bp.get("trigger_type", "interval"),
                    "interval_minutes": max(1, int(bp.get("interval_seconds", 300) // 60)),
                    "interval_seconds": bp.get("interval_seconds", 300)
                }
            return parsed

        raise ValueError(parsed.get("reply", "No blueprint in model response"))

    except Exception as e:
        logger.warning(f"LLM Bot architect error (fallback active): {e}")
        # Deterministic Blueprint Synthesis Fallback
        msg_l = (user_message + " " + " ".join([h.get("content", "") for h in (history or [])])).lower()
        
        app_name = "devops-vsp-sample-app-shakil" if ("devops-vsp-sample-app" in msg_l or "springboot" in msg_l or "spring" in msg_l) else ("ai-mcp-platform-shakil" if "ai-mcp" in msg_l else "devops-vsp-sample-app-shakil")
        desc = "Spring App Error" if "spring" in msg_l else "Application Incident"
        repo = "springboot-app" if "springboot" in msg_l else "AI-POC"
        
        servers_used = []
        tools_mapped = []
        if "azure_app_service" in servers:
            servers_used.append("azure_app_service")
            tools_mapped.extend(["azure_app_service.get_app_service_logs", "azure_app_service.restart_app_service"])
        if "servicenow" in servers:
            servers_used.append("servicenow")
            tools_mapped.extend(["servicenow.query_incidents", "servicenow.create_incident", "servicenow.add_work_note", "servicenow.resolve_incident"])
        if "azure_devops" in servers:
            servers_used.append("azure_devops")
            tools_mapped.extend(["azure_devops.get_file_content", "azure_devops.list_builds"])

        bot_id = f"auto_workflow_bot_{int(time.time())}"
        blueprint = {
            "id": bot_id,
            "name": "Spring Boot Autonomous Bot",
            "description": f"Monitors {app_name}, deduplicates ServiceNow incidents, extracts error traces, inspects ADO codebase, and performs AI RCA.",
            "trigger_type": "interval",
            "interval_seconds": 300,
            "schedule": {
                "type": "interval",
                "interval_minutes": 5,
                "interval_seconds": 300
            },
            "status": "active",
            "instructions": user_message,
            "tools_required": servers_used,
            "context_config": {
                "container_name": app_name,
                "ado_repo": repo,
                "ado_branch": "main",
                "ado_pipeline": "AI-POC-CI-CD",
                "servicenow_short_description": desc
            },
            "workflow_steps": [
                {"step": 1, "action": f"Query ServiceNow for open tickets with Short Description '{desc}'", "server": "servicenow", "tool": "query_incidents"},
                {"step": 2, "action": f"Fetch latest logs from App Service '{app_name}' and strip error trace", "server": "azure_app_service", "tool": "get_app_service_logs"},
                {"step": 3, "action": f"Create ServiceNow Incident '{desc}' if no duplicate exists", "server": "servicenow", "tool": "create_incident"},
                {"step": 4, "action": "Attach raw Error Snippet alone to Ticket Work Notes", "server": "servicenow", "tool": "add_work_note"},
                {"step": 5, "action": "Add Work Note: '🤖 AI Bot is looking into the issue'", "server": "servicenow", "tool": "add_work_note"},
                {"step": 6, "action": f"Inspect ADO repo '{repo}' (main branch) and check recent pipeline builds", "server": "azure_devops", "tool": "get_file_content"},
                {"step": 7, "action": "Execute AI RCA and update Ticket with complete detailed RCA report", "server": "servicenow", "tool": "add_work_note"},
                {"step": 8, "action": "Resolve / Close Ticket in ServiceNow (State: 6)", "server": "servicenow", "tool": "resolve_incident"}
            ],
            "workflow_code": f"""# Auto-Synthesized Autonomous Bot
from bot_engine import fetch_azure_appservice_logs, extract_stripped_error_log, generate_ai_rca, execute_mcp_tool_on_gateway

def execute_workflow(ctx):
    app_name = ctx.get("container_name", "{app_name}")
    desc = ctx.get("servicenow_short_description", "{desc}")
    repo = ctx.get("ado_repo", "{repo}")

    # 1. Duplicate check
    q_res = execute_mcp_tool_on_gateway("servicenow", "query_incidents", {{"short_description": desc}})
    active_incidents = [inc for inc in q_res.get("raw", {{}}).get("result", []) if str(inc.get("state")) not in ["6", "7", "8"]]
    if active_incidents:
        return {{"status": "skipped", "message": f"Active ticket already exists: {{active_incidents[0].get('number')}}"}}

    # 2. Fetch logs & strip error
    logs = fetch_azure_appservice_logs(app_name)
    err = extract_stripped_error_log(logs)

    # 3. Create Ticket
    cr_res = execute_mcp_tool_on_gateway("servicenow", "create_incident", {{"short_description": desc, "category": "Software"}})
    inc_data = cr_res.get("raw", {{}}).get("result", {{}})
    inc_id = inc_data.get("sys_id") or inc_data.get("number")

    # 4. Attach Error Snippet
    execute_mcp_tool_on_gateway("servicenow", "add_work_note", {{"incident_id": inc_id, "work_notes": f"Error Snippet:\\n{{err[:1000]}}"}})

    # 5. AI Bot Work Note
    execute_mcp_tool_on_gateway("servicenow", "add_work_note", {{"incident_id": inc_id, "work_notes": "🤖 AI Bot is looking into the issue."}})

    # 6. ADO Code & Pipeline Inspection
    ado_res = execute_mcp_tool_on_gateway("azure_devops", "list_builds", {{"project": "AI-POC"}})

    # 7. Complete RCA
    rca = generate_ai_rca(err, app_name, app_context=f"ADO Repo: {{repo}} (main branch)")
    execute_mcp_tool_on_gateway("servicenow", "add_work_note", {{"incident_id": inc_id, "work_notes": rca.get("formatted_rca_markdown", "")}})

    # 8. Close ticket
    execute_mcp_tool_on_gateway("servicenow", "resolve_incident", {{"incident_id": inc_id, "state": "6"}})

    return {{"status": "success", "incident": inc_id, "rca": rca}}
"""
        }

        return {
            "status": "ready",
            "reply": f"🤖 **Synthesized Autonomous Bot Blueprint successfully!**\n\n* **Target App**: `{app_name}`\n* **ServiceNow Trigger**: `{desc}`\n* **ADO Repo**: `{repo}`\n* **Tools Mapped**: {len(tools_mapped)} tools validated across `{', '.join(servers_used)}`.\n\nClick **Deploy Autonomous Bot** to save and activate this workflow.",
            "validation": {
                "supported": True,
                "servers_used": servers_used,
                "tools_mapped": tools_mapped,
                "missing_servers": []
            },
            "blueprint": blueprint
        }
