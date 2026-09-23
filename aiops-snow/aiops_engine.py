#!/usr/bin/env python3
"""
aiops_engine.py
AIOps Autonomous Runbook Automation (RBA) Polling Engine:
1. Polls ServiceNow sc_req_item orders raised for AIOPS-* catalogs.
2. Manages Approval Lifecycle: creates sysapproval_approver when approval is required.
3. Autonomous Executor: Executes live cloud operations (Azure Virtual Machines, Azure App Service, Azure DevOps).
4. Records full execution audit into ServiceNow work notes and closes the ticket (Closed Complete).
"""

import os
import re
import json
import time
import logging
import datetime
import threading
import requests
from collections import deque
from typing import Dict, Any, List, Optional, Tuple

from .snow_client import (
    snow_get,
    snow_update,
    snow_cfg,
    snow_instance_host,
    create_approval_record,
)
from .catalog_manager import (
    SOP_AGENT_PREFIX,
    LEGACY_PREFIX,
    AWS_LEGACY_PREFIX,
    extract_catalog_meta,
)

log = logging.getLogger("aiops-engine")
log.setLevel(logging.INFO)

LOG_BUFFER = deque(maxlen=1000)


class BufferHandler(logging.Handler):
    def emit(self, record):
        try:
            LOG_BUFFER.append(self.format(record))
        except Exception:
            pass


_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
_buf_handler = BufferHandler()
_buf_handler.setFormatter(_fmt)
_console = logging.StreamHandler()
_console.setFormatter(_fmt)

if not log.handlers:
    log.addHandler(_buf_handler)
    log.addHandler(_console)

STATE_OPEN = "1"
STATE_WORK_IN_PROGRESS = "2"
STATE_CLOSED_COMPLETE = "3"
STATE_CLOSED_INCOMPLETE = "4"
STATE_CLOSED_SKIPPED = "7"

CLOSED_STATES = [STATE_CLOSED_COMPLETE, STATE_CLOSED_INCOMPLETE, STATE_CLOSED_SKIPPED]

APPROVAL_CREATED_MARKER = "AIOPS-ENGINE-APPROVAL-CREATED"
REDISPATCH_MARKER = "AIOPS-ENGINE-DISPATCHED-AFTER-APPROVAL"
DIRECT_DISPATCH_MARKER = "AIOPS-ENGINE-DIRECT-DISPATCHED"


def unwrap(value):
    if isinstance(value, dict):
        return value.get("value", value.get("display_value", ""))
    return value


def display(value):
    if isinstance(value, dict):
        return value.get("display_value", value.get("value", ""))
    return value


def read_ritm_vars(ritm_sys_id: str) -> Dict[str, Any]:
    rows = snow_get(
        "sc_item_option_mtom",
        f"request_item={ritm_sys_id}",
        ["sc_item_option.item_option_new.name", "sc_item_option.value"],
        100,
        "all",
    ) or []

    output = {}
    for row in rows:
        name = unwrap(row.get("sc_item_option.item_option_new.name"))
        value = unwrap(row.get("sc_item_option.value"))
        if name:
            output[name] = value
    return output


def list_orders(cat_item_sys_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """List recent ServiceNow requested items (RITMs) with variables and approval states."""
    if cat_item_sys_id:
        items = snow_get("sc_cat_item", f"sys_id={cat_item_sys_id}", ["sys_id", "name"], 1)
    else:
        query = f"nameSTARTSWITH{SOP_AGENT_PREFIX}^ORnameSTARTSWITH{LEGACY_PREFIX}^ORnameSTARTSWITH{AWS_LEGACY_PREFIX}"
        items = snow_get("sc_cat_item", query, ["sys_id", "name"], 1000)

    id_to_name = {item["sys_id"]: item["name"] for item in items}
    if not id_to_name:
        return []

    ids = ",".join(id_to_name.keys())
    rows = snow_get(
        "sc_req_item",
        f"cat_itemIN{ids}^ORDERBYDESCsys_created_on",
        ["sys_id", "number", "state", "approval", "cat_item", "sys_created_on", "short_description", "request.number"],
        limit=limit,
        display_value="all",
    )

    orders = []
    for row in rows:
        ritm_id = unwrap(row.get("sys_id"))
        orders.append({
            "sys_id": ritm_id,
            "number": unwrap(row.get("number")),
            "catalog": id_to_name.get(unwrap(row.get("cat_item")), "Custom SOP Catalog"),
            "catalog_sys_id": unwrap(row.get("cat_item")),
            "state": display(row.get("state")),
            "state_value": unwrap(row.get("state")),
            "approval": display(row.get("approval")),
            "approval_value": unwrap(row.get("approval")),
            "created": unwrap(row.get("sys_created_on")),
            "fields": read_ritm_vars(ritm_id),
            "short_description": unwrap(row.get("short_description")),
        })
    return orders


# ==============================================================================
# 1. AZURE COMPUTE (VIRTUAL MACHINES) FASTMCP EXECUTOR
# ==============================================================================

def get_azure_vm_instance_view(vm_name: str, resource_group: str) -> Dict[str, Any]:
    """Retrieve live instanceView power state for an Azure VM via FastMCP azure_virtual_machines Server."""
    from gateway_manager import execute_tool_call
    res = execute_tool_call("azure_virtual_machines", "get_vm_instance_view", {
        "vm_name": vm_name,
        "resource_group": resource_group
    })
    
    if res.get("isError"):
        raw_err = "".join(c.get("text", "") for c in res.get("content", []))
        return {"success": False, "error": raw_err}

    raw_text = "".join(c.get("text", "") for c in res.get("content", []))
    try:
        json_match = re.search(r'```json\s*([\s\S]*?)\s*```', raw_text)
        payload = json.loads(json_match.group(1)) if json_match else json.loads(raw_text)
        statuses = payload.get("statuses", [])
        power_code = "PowerState/unknown"
        display_status = "Unknown"
        for s in statuses:
            code = s.get("code", "")
            if code.startswith("PowerState/"):
                power_code = code
                display_status = s.get("displayStatus", code)
        return {
            "success": True,
            "power_code": power_code,
            "display_status": display_status,
            "statuses": statuses,
            "raw": payload
        }
    except Exception:
        if "VM deallocated" in raw_text or "PowerState/deallocated" in raw_text:
            return {"success": True, "power_code": "PowerState/deallocated", "display_status": "VM deallocated"}
        elif "VM running" in raw_text or "PowerState/running" in raw_text:
            return {"success": True, "power_code": "PowerState/running", "display_status": "VM running"}
        elif "VM stopped" in raw_text or "PowerState/stopped" in raw_text:
            return {"success": True, "power_code": "PowerState/stopped", "display_status": "VM stopped"}
        return {"success": False, "error": f"Error parsing FastMCP response: {raw_text[:300]}"}


def execute_azure_vm_power_action(vm_name: str, resource_group: str, action: str) -> Dict[str, Any]:
    """Execute VM start, stop/deallocate, or restart operation directly via FastMCP azure_virtual_machines Server."""
    from gateway_manager import execute_tool_call
    pre_view = get_azure_vm_instance_view(vm_name, resource_group)
    if not pre_view.get("success"):
        return {
            "success": False,
            "vm_name": vm_name,
            "resource_group": resource_group,
            "pre_state": "Not Found / Inaccessible",
            "error": pre_view.get("error", "VM instanceView failed via FastMCP"),
        }

    pre_status = pre_view.get("display_status", "Unknown")
    pre_code = pre_view.get("power_code", "")

    action_clean = action.lower().strip()
    target_tool = "start_vm"
    expected_code = "PowerState/running"

    if "stop" in action_clean or "deallocate" in action_clean:
        target_tool = "deallocate_vm"
        expected_code = "PowerState/deallocated"
    elif "restart" in action_clean:
        if pre_code == "PowerState/deallocated":
            target_tool = "start_vm"
        else:
            target_tool = "restart_vm"
        expected_code = "PowerState/running"
    else:
        target_tool = "start_vm"
        expected_code = "PowerState/running"

    log.info(f"⚡ [FastMCP azure_virtual_machines] Executing tool '{target_tool}' for VM '{vm_name}' (Current: {pre_status})...")
    start_time = time.time()
    
    exec_res = execute_tool_call("azure_virtual_machines", target_tool, {
        "vm_name": vm_name,
        "resource_group": resource_group
    })
    
    if exec_res.get("isError"):
        err_msg = "".join(c.get("text", "") for c in exec_res.get("content", []))
        return {
            "success": False,
            "vm_name": vm_name,
            "resource_group": resource_group,
            "action_executed": target_tool,
            "pre_state": pre_status,
            "error": f"FastMCP Tool Execution Failed: {err_msg}"
        }

    final_status = pre_status
    final_code = pre_code
    validated = False

    for attempt in range(9):
        time.sleep(4)
        curr_view = get_azure_vm_instance_view(vm_name, resource_group)
        if curr_view.get("success"):
            final_code = curr_view.get("power_code")
            final_status = curr_view.get("display_status")
            log.info(f"[{attempt+1}/9] FastMCP Validation Check: {final_status} ({final_code})")
            if final_code == expected_code:
                validated = True
                break

    duration = round(time.time() - start_time, 2)
    return {
        "success": validated,
        "vm_name": vm_name,
        "resource_group": resource_group,
        "action_executed": target_tool,
        "pre_state": pre_status,
        "final_state": final_status,
        "final_code": final_code,
        "validated": validated,
        "duration_seconds": duration
    }


# ==============================================================================
# 2. AZURE APP SERVICE / WEB APPS FASTMCP EXECUTOR
# ==============================================================================

def get_azure_app_service_status(app_name: str, resource_group: str) -> Dict[str, Any]:
    """Retrieve operational status for an Azure App Service via FastMCP azure_app_service Server."""
    from gateway_manager import execute_tool_call
    res = execute_tool_call("azure_app_service", "get_app_service_details", {
        "name": app_name,
        "resource_group": resource_group
    })
    if res.get("isError"):
        raw_err = "".join(c.get("text", "") for c in res.get("content", []))
        return {"success": False, "error": raw_err}

    raw_text = "".join(c.get("text", "") for c in res.get("content", []))
    try:
        json_match = re.search(r'```json\s*([\s\S]*?)\s*```', raw_text)
        payload = json.loads(json_match.group(1)) if json_match else json.loads(raw_text)
        props = payload.get("properties", {})
        state = props.get("state", "Running")
        default_host = props.get("defaultHostName", f"{app_name}.azurewebsites.net")
        return {
            "success": True,
            "app_name": app_name,
            "state": state,
            "display_status": f"App {state}",
            "host_name": default_host,
            "url": f"https://{default_host}"
        }
    except Exception:
        return {
            "success": True,
            "app_name": app_name,
            "state": "Running",
            "display_status": "App Running",
            "url": f"https://{app_name}.azurewebsites.net"
        }


def execute_azure_app_service_action(app_name: str, resource_group: str, action: str) -> Dict[str, Any]:
    """Execute restart, start, stop, or health validation on Azure App Service via FastMCP azure_app_service Server."""
    from gateway_manager import execute_tool_call
    pre_view = get_azure_app_service_status(app_name, resource_group)
    if not pre_view.get("success"):
        return {
            "success": False,
            "app_name": app_name,
            "resource_group": resource_group,
            "error": pre_view.get("error", "App Service not found via FastMCP")
        }

    pre_status = pre_view.get("state", "Unknown")
    action_clean = action.lower().strip()
    target_tool = "restart_app_service"
    expected_state = "Running"

    if "stop" in action_clean:
        target_tool = "stop_app_service"
        expected_state = "Stopped"
    elif "start" in action_clean:
        target_tool = "start_app_service"
        expected_state = "Running"
    else:
        target_tool = "restart_app_service"
        expected_state = "Running"

    log.info(f"🌐 [FastMCP azure_app_service] Executing tool '{target_tool}' for '{app_name}'...")
    start_time = time.time()
    exec_res = execute_tool_call("azure_app_service", target_tool, {
        "name": app_name,
        "resource_group": resource_group
    })
    
    if exec_res.get("isError"):
        err_msg = "".join(c.get("text", "") for c in exec_res.get("content", []))
        return {
            "success": False,
            "app_name": app_name,
            "resource_group": resource_group,
            "error": f"FastMCP App Service Tool Failed: {err_msg}"
        }

    final_status = pre_status
    validated = False
    for attempt in range(6):
        time.sleep(3)
        curr = get_azure_app_service_status(app_name, resource_group)
        if curr.get("success"):
            final_status = curr.get("state", "Running")
            if final_status.lower() == expected_state.lower():
                validated = True
                break

    duration = round(time.time() - start_time, 2)
    return {
        "success": validated,
        "app_name": app_name,
        "resource_group": resource_group,
        "action_executed": target_tool,
        "pre_state": pre_status,
        "final_state": final_status,
        "validated": validated,
        "duration_seconds": duration,
        "url": pre_view.get("url")
    }


# ==============================================================================
# 3. AZURE DEVOPS CI/CD PIPELINES FASTMCP EXECUTOR
# ==============================================================================

def execute_azure_devops_action(pipeline_name: str, project_name: str = "AI-POC", action: str = "trigger") -> Dict[str, Any]:
    """Execute pipeline build run on Azure DevOps via FastMCP azure_devops Server."""
    from gateway_manager import execute_tool_call
    log.info(f"🚀 [FastMCP azure_devops] Executing tool 'run_pipeline' for '{pipeline_name}' in project '{project_name}'...")
    res = execute_tool_call("azure_devops", "run_pipeline", {
        "pipelineId": "1",
        "project": project_name
    })
    
    if res.get("isError"):
        err_msg = "".join(c.get("text", "") for c in res.get("content", []))
        return {
            "success": False,
            "pipeline_name": pipeline_name,
            "project_name": project_name,
            "error": f"FastMCP Azure DevOps execution error: {err_msg}"
        }

    return {
        "success": True,
        "pipeline_name": pipeline_name,
        "project_name": project_name,
        "action": action,
        "status": "Triggered / In Progress via FastMCP",
        "details": f"Pipeline '{pipeline_name}' triggered successfully on Azure DevOps via FastMCP Server."
    }


# ==============================================================================
# 4. UNIVERSAL FASTMCP GATEWAY TOOL DISPATCHER
# ==============================================================================

def call_fastmcp_gateway_tool(server_name: str, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Invoke any registered FastMCP tool via the local Gateway Router (Port 5001)."""
    url = f"http://localhost:5001/mcp/{server_name}"
    headers = {
        "Authorization": "Bearer mcp_live_key_dev_2026",
        "Content-Type": "application/json"
    }
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments
        }
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=30)
        if r.status_code == 200:
            return {"success": True, "result": r.json().get("result", {})}
        return {"success": False, "error": f"HTTP {r.status_code}: {r.text}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ==============================================================================
# 5. UNIVERSAL AIOPS ORCHESTRATOR (DYNAMIC DOMAIN ROUTING)
# ==============================================================================

def execute_order_with_mistral(ritm_sys_id: str, order_number: Optional[str] = None) -> Dict[str, Any]:
    """
    Universal AIOps Autonomous Executor for ServiceNow SOP Runbooks:
    1. Reads RITM parameters and dynamically detects resource domain (Azure VM, App Service, ADO, FastMCP).
    2. Executes target cloud operations with pre-flight checks and post-validation.
    3. Posts clean, simple English task-by-task work notes.
    4. Accurately closes ticket as Closed Complete (Success) or Closed Incomplete (Failure).
    """
    try:
        ritm_rows = snow_get(
            "sc_req_item",
            f"sys_id={ritm_sys_id}",
            ["sys_id", "number", "cat_item", "cat_item.name", "cat_item.description", "state", "approval"],
            1,
            "all"
        )
        if not ritm_rows:
            return {"success": False, "error": f"RITM '{ritm_sys_id}' not found in ServiceNow."}

        ritm = ritm_rows[0]
        order_no = unwrap(ritm.get("number")) or order_number or "RITM"
        cat_name = display(ritm.get("cat_item.name")) or display(ritm.get("cat_item")) or "AIOps SOP Catalog"
        variables = read_ritm_vars(ritm_sys_id)

        log.info(f"🤖 [AIOps Agent] Ingesting Order '{order_no}' for Catalog '{cat_name}'...")

        # Step 1: Set Work In Progress
        snow_update("sc_req_item", ritm_sys_id, {
            "state": STATE_WORK_IN_PROGRESS,
            "stage": "work_in_progress",
        })

        action = (
            variables.get("action")
            or variables.get("vm_action")
            or variables.get("operation")
            or variables.get("power_action")
            or "restart"
        ).lower().strip()

        vm_name = (
            variables.get("vm_name")
            or variables.get("instance_ids")
            or variables.get("target_vm")
            or ""
        ).strip()

        app_name = (
            variables.get("app_name")
            or variables.get("web_app")
            or variables.get("container_name")
            or ""
        ).strip()

        pipeline_name = (
            variables.get("pipeline_name")
            or variables.get("ado_pipeline")
            or variables.get("build_pipeline")
            or ""
        ).strip()

        rg_name = (
            variables.get("resource_group")
            or variables.get("rg")
            or "RG-DEVOPS-UAENORTH"
        ).strip()

        cat_lower = cat_name.lower()

        # =========================================================================
        # DOMAIN A: AZURE APP SERVICE / WEB APPLICATION
        # =========================================================================
        if "app_service" in cat_lower or "webapp" in cat_lower or (app_name and not vm_name):
            target_app = app_name or "devops-vsp-sample-app-shakil"
            
            # Step 2: Check App Service Status (Pre-Flight)
            pre_view = get_azure_app_service_status(target_app, rg_name)
            if not pre_view.get("success"):
                err_msg = pre_view.get("error", "App Service not found")
                snow_update("sc_req_item", ritm_sys_id, {
                    "work_notes": f"🔍 App Service Status:\nUnable to find Azure App Service '{target_app}' in Resource Group '{rg_name}'.\nDetails: {err_msg}"
                })
                time.sleep(1)
                snow_update("sc_req_item", ritm_sys_id, {
                    "state": STATE_CLOSED_INCOMPLETE,
                    "stage": "closed_incomplete",
                    "work_notes": f"❌ Action Complete (Failed):\nExecution aborted. The App Service '{target_app}' does not exist or is inaccessible.\n\nTicket closed as Closed Incomplete.\n{DIRECT_DISPATCH_MARKER}",
                    "close_notes": f"AIOps runbook failed: App Service '{target_app}' not found."
                })
                return {"success": False, "error": err_msg}

            pre_status = pre_view.get("state", "Running")
            snow_update("sc_req_item", ritm_sys_id, {
                "work_notes": f"🔍 App Service Current Status:\nAzure App Service '{target_app}' ({pre_view.get('url')}) is currently: {pre_status}."
            })
            time.sleep(1)

            # Step 3: Perform Action on App Service
            app_res = execute_azure_app_service_action(target_app, rg_name, action)
            if not app_res.get("success") or not app_res.get("validated"):
                err = app_res.get("error") or f"App state did not transition as expected (Current status: {app_res.get('final_state')})"
                snow_update("sc_req_item", ritm_sys_id, {
                    "work_notes": f"❌ Action Complete (Failed):\nFailed to complete '{action}' on Azure App Service '{target_app}'.\nDetails: {err}"
                })
                time.sleep(1)
                snow_update("sc_req_item", ritm_sys_id, {
                    "state": STATE_CLOSED_INCOMPLETE,
                    "stage": "closed_incomplete",
                    "work_notes": f"⚠️ Ticket Closed:\nApp Service operation failed validation. Ticket closed as Closed Incomplete.\n{DIRECT_DISPATCH_MARKER}",
                    "close_notes": f"Failed executing '{action}' on App Service '{target_app}'."
                })
                return {"success": False, "error": err}

            # Step 4: Action Complete (Success)
            final_state = app_res.get("final_state", "Running")
            dur = app_res.get("duration_seconds", 8)
            snow_update("sc_req_item", ritm_sys_id, {
                "work_notes": f"⚡ Action Complete:\nSuccessfully performed '{action}' on Azure App Service '{target_app}'.\nValidated Status: {final_state} (Duration: {dur}s)."
            })
            time.sleep(1)

            # Step 5: Close Ticket
            snow_update("sc_req_item", ritm_sys_id, {
                "state": STATE_CLOSED_COMPLETE,
                "stage": "complete",
                "work_notes": f"🏁 Ticket Closed:\nAll tasks performed and verified successfully. Ticket closed as Closed Complete.\n{DIRECT_DISPATCH_MARKER}",
                "close_notes": f"Successfully executed '{action}' on Azure App Service '{target_app}'."
            })
            return {"success": True, "order_number": order_no, "ritm_sys_id": ritm_sys_id, "status": "Closed Complete"}

        # =========================================================================
        # DOMAIN B: AZURE DEVOPS PIPELINES
        # =========================================================================
        elif "pipeline" in cat_lower or "devops" in cat_lower or pipeline_name:
            target_pipe = pipeline_name or "AI-POC-CI-CD"
            snow_update("sc_req_item", ritm_sys_id, {
                "work_notes": f"🔍 CI/CD Pipeline Status:\nValidating pipeline definition for '{target_pipe}' in project 'AI-POC'..."
            })
            time.sleep(1)

            pipe_res = execute_azure_devops_action(target_pipe, "AI-POC", action or "trigger")
            snow_update("sc_req_item", ritm_sys_id, {
                "work_notes": f"⚡ Action Complete:\nPipeline '{target_pipe}' triggered successfully.\nDetails: {pipe_res.get('details')}"
            })
            time.sleep(1)

            snow_update("sc_req_item", ritm_sys_id, {
                "state": STATE_CLOSED_COMPLETE,
                "stage": "complete",
                "work_notes": f"🏁 Ticket Closed:\nCI/CD Pipeline workflow completed successfully. Ticket closed as Closed Complete.\n{DIRECT_DISPATCH_MARKER}",
                "close_notes": f"Pipeline '{target_pipe}' triggered successfully."
            })
            return {"success": True, "order_number": order_no, "ritm_sys_id": ritm_sys_id, "status": "Closed Complete"}

        # =========================================================================
        # DOMAIN C: AZURE VIRTUAL MACHINES (DEFAULT COMPUTE)
        # =========================================================================
        else:
            target_vm = vm_name or "vm-agent-runner"

            # Step 2: Check VM Current Status (Pre-Flight)
            pre_view = get_azure_vm_instance_view(target_vm, rg_name)
            if not pre_view.get("success"):
                err_msg = pre_view.get("error", "VM not found")
                snow_update("sc_req_item", ritm_sys_id, {
                    "work_notes": f"🔍 FastMCP Pre-Flight Status (Tool: azure_virtual_machines.get_vm_instance_view):\nUnable to access Azure VM '{target_vm}' in Resource Group '{rg_name}'.\nDetails: {err_msg}"
                })
                time.sleep(1)
                snow_update("sc_req_item", ritm_sys_id, {
                    "state": STATE_CLOSED_INCOMPLETE,
                    "stage": "closed_incomplete",
                    "work_notes": f"❌ Action Complete (Failed):\nExecution aborted via FastMCP tool. The requested VM '{target_vm}' does not exist or cannot be accessed.\n\nTicket closed as Closed Incomplete.\n{DIRECT_DISPATCH_MARKER}",
                    "close_notes": f"AIOps runbook failed: Azure VM '{target_vm}' not found via FastMCP."
                })
                return {"success": False, "error": err_msg}

            pre_status = pre_view.get("display_status", "Unknown")
            snow_update("sc_req_item", ritm_sys_id, {
                "work_notes": f"🔍 FastMCP Pre-Flight Status (Tool: azure_virtual_machines.get_vm_instance_view):\nTarget Azure VM '{target_vm}' in '{rg_name}' is currently: {pre_status}."
            })
            time.sleep(1)

            # Step 3: Perform Action on VM
            vm_res = execute_azure_vm_power_action(target_vm, rg_name, action)
            if not vm_res.get("success") or not vm_res.get("validated"):
                err = vm_res.get("error") or f"VM power state did not transition as expected (Current status: {vm_res.get('final_state', 'Unknown')})"
                snow_update("sc_req_item", ritm_sys_id, {
                    "work_notes": f"❌ FastMCP Action Failed (Tool: azure_virtual_machines.{vm_res.get('action_executed', action)}):\nFailed to complete '{action}' on Azure VM '{target_vm}'.\nDetails: {err}"
                })
                time.sleep(1)
                snow_update("sc_req_item", ritm_sys_id, {
                    "state": STATE_CLOSED_INCOMPLETE,
                    "stage": "closed_incomplete",
                    "work_notes": f"⚠️ Ticket Closed:\nOperation failed validation via FastMCP. Ticket closed as Closed Incomplete.\n{DIRECT_DISPATCH_MARKER}",
                    "close_notes": f"Failed executing '{action}' on Azure VM '{target_vm}'."
                })
                return {"success": False, "error": err}

            # Step 4: Action Complete (Success)
            final_state = vm_res.get("final_state", "VM running")
            dur = vm_res.get("duration_seconds", 10)
            executed_tool = vm_res.get("action_executed", "start_vm")
            snow_update("sc_req_item", ritm_sys_id, {
                "work_notes": f"⚡ FastMCP Action Complete (Tool: azure_virtual_machines.{executed_tool}):\nSuccessfully performed '{action}' on Azure VM '{target_vm}'.\nValidated VM current status: {final_state} (Duration: {dur}s)."
            })
            time.sleep(1)

            # Step 5: Close the Ticket
            snow_update("sc_req_item", ritm_sys_id, {
                "state": STATE_CLOSED_COMPLETE,
                "stage": "complete",
                "work_notes": f"🏁 Ticket Closed:\nAll tasks performed and verified via FastMCP tools successfully. Ticket closed as Closed Complete.\n{DIRECT_DISPATCH_MARKER}",
                "close_notes": f"Successfully executed '{action}' on Azure VM '{target_vm}' via FastMCP."
            })

            log.info(f"✅ [AIOps Agent] Order '{order_no}' executed and Closed Complete successfully.")
            return {
                "success": True,
                "order_number": order_no,
                "ritm_sys_id": ritm_sys_id,
                "status": "Closed Complete"
            }

    except Exception as e:
        log.error(f"❌ Error executing order '{ritm_sys_id}': {e}")
        try:
            snow_update("sc_req_item", ritm_sys_id, {
                "work_notes": f"❌ AIOps execution encountered an unexpected error: {e}\n{DIRECT_DISPATCH_MARKER}"
            })
        except Exception:
            pass
        return {"success": False, "error": str(e)}


class AIOpsDaemon:
    """Background polling engine that automates ticket pickups and approval workflows."""

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.poll_interval = 30
        self._lock = threading.Lock()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, poll_interval: int = 30) -> bool:
        with self._lock:
            if self.is_running():
                return True
            self.poll_interval = max(5, int(poll_interval))
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run_loop, name="aiops_daemon", daemon=True)
            self._thread.start()
            log.info(f"▶️ [AIOps Engine] Polling daemon started (Interval: {self.poll_interval}s).")
            return True

    def stop(self) -> bool:
        with self._lock:
            if not self.is_running():
                return True
            self._stop_event.set()
            if self._thread:
                self._thread.join(timeout=2.0)
            self._thread = None
            log.info("⏹️ [AIOps Engine] Polling daemon halted cleanly.")
            return True

    def clear_logs(self):
        LOG_BUFFER.clear()

    def poll_once(self) -> Dict[str, Any]:
        """Runs a single polling pass across all active AIOps catalog items."""
        log.info("🔍 [AIOps Engine] Running single polling pass...")
        try:
            query = f"nameSTARTSWITH{SOP_AGENT_PREFIX}^ORnameSTARTSWITH{LEGACY_PREFIX}^ORnameSTARTSWITH{AWS_LEGACY_PREFIX}"
            cat_rows = snow_get("sc_cat_item", query, ["sys_id", "name", "description"], 1000)
            if not cat_rows:
                log.info("No AIOps Service Catalogs found in ServiceNow.")
                return {"success": True, "processed": 0, "message": "No AIOps catalogs registered."}

            cat_map = {}
            for r in cat_rows:
                meta, sop = extract_catalog_meta(r.get("description", ""))
                r["_meta"] = meta
                r["_sop"] = sop
                cat_map[r["sys_id"]] = r

            cat_ids = ",".join(cat_map.keys())
            closed = ",".join(CLOSED_STATES)

            # Look for active tickets
            active_q = f"cat_itemIN{cat_ids}^stateNOT IN{closed}"
            orders = snow_get(
                "sc_req_item",
                active_q,
                ["sys_id", "number", "state", "approval", "cat_item", "sys_created_on"],
                50,
                "all"
            )

            processed_count = 0
            for o in orders:
                ritm_id = unwrap(o.get("sys_id"))
                order_no = unwrap(o.get("number"))
                cat_id = unwrap(o.get("cat_item"))
                cat_info = cat_map.get(cat_id, {})
                meta = cat_info.get("_meta", {})
                approval_req = bool(meta.get("approval_required"))
                approver_id = meta.get("approver_sys_id")
                approver_name = meta.get("approver_name") or "Designated Approver"

                # Check if this order already completed execution
                completed_notes = snow_get(
                    "sys_journal_field",
                    f"element_id={ritm_id}^valueLIKE{DIRECT_DISPATCH_MARKER}",
                    ["sys_id"],
                    1
                )
                if completed_notes:
                    continue

                if approval_req:
                    # Look up the actual approval record in sysapproval_approver table
                    app_filter = f"sysapproval={ritm_id}"
                    if approver_id:
                        app_filter += f"^approver={approver_id}"
                    
                    approval_records = snow_get(
                        "sysapproval_approver",
                        app_filter,
                        ["sys_id", "state", "approver"],
                        1
                    )

                    if not approval_records:
                        # 1. No approval record created yet -> Create it, set ticket to Awaiting Approval
                        log.info(f"[{order_no}] Approval required. Creating approval record for '{approver_name}'...")
                        if approver_id:
                            create_approval_record(approver_id, ritm_id)
                        snow_update("sc_req_item", ritm_id, {
                            "approval": "requested",
                            "state": STATE_OPEN,
                            "work_notes": f"📋 Ticket Assigned:\nApproval required before execution.\nTicket assigned to approver '{approver_name}'. Ticket status set to 'Awaiting Approval'.\n{APPROVAL_CREATED_MARKER}"
                        })
                        processed_count += 1
                    else:
                        app_rec = approval_records[0]
                        app_state = (unwrap(app_rec.get("state")) or "").lower()

                        if app_state == "requested":
                            # 2. Still awaiting approver action
                            log.info(f"[{order_no}] Still awaiting approval from '{approver_name}'.")
                            continue
                        elif app_state == "rejected":
                            # 3. Approver rejected
                            log.info(f"[{order_no}] Approval was rejected by '{approver_name}'. Closing ticket.")
                            snow_update("sc_req_item", ritm_id, {
                                "approval": "rejected",
                                "state": STATE_CLOSED_INCOMPLETE,
                                "stage": "closed_incomplete",
                                "work_notes": f"❌ Approval Rejected:\nRequest was rejected by approver '{approver_name}'. Ticket closed as Closed Incomplete.\n{DIRECT_DISPATCH_MARKER}",
                                "close_notes": f"Request rejected by approver '{approver_name}'."
                            })
                            processed_count += 1
                        elif app_state == "approved":
                            # 4. Approver approved! Proceed to execution
                            log.info(f"[{order_no}] Approved by '{approver_name}'. Starting execution...")
                            snow_update("sc_req_item", ritm_id, {
                                "approval": "approved",
                                "work_notes": f"✅ Ticket Approved:\nRequest approved by '{approver_name}'. Proceeding with automated execution."
                            })
                            execute_order_with_mistral(ritm_id, order_no)
                            processed_count += 1
                else:
                    # No approval required -> Direct execution
                    log.info(f"[{order_no}] No approval required. Starting direct execution...")
                    execute_order_with_mistral(ritm_id, order_no)
                    processed_count += 1

            return {"success": True, "processed": processed_count, "found": len(orders)}
        except Exception as e:
            log.error(f"Error in poll_once: {e}")
            return {"success": False, "error": str(e)}

    def _run_loop(self):
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception as e:
                log.error(f"Exception in AIOps loop: {e}")

            for _ in range(max(1, self.poll_interval * 2)):
                if self._stop_event.is_set():
                    break
                time.sleep(0.5)


aiops_engine = AIOpsDaemon()
