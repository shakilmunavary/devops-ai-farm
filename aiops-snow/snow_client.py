#!/usr/bin/env python3
"""
snow_client.py
ServiceNow REST API client wrapper.
Handles basic auth, table queries, record creation/updating, attachments, and user search.
"""

import os
import json
import logging
import requests

try:
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv(usecwd=True))
except ImportError:
    pass

log = logging.getLogger("aiops-snow")

DEFAULT_SNOW_INSTANCE = "dev392242.service-now.com"
DEFAULT_SNOW_USER = "mcp_admin"
DEFAULT_SNOW_PASSWORD = "Magic@100"


def normalize_instance(value: str) -> str:
    value = (value or "").strip()
    value = value.replace("https://", "").replace("http://", "")
    return value.rstrip("/")


def snow_cfg():
    instance = normalize_instance(os.getenv("SNOW_INSTANCE", DEFAULT_SNOW_INSTANCE))
    user = os.getenv("SNOW_USER", DEFAULT_SNOW_USER)
    password = os.getenv("SNOW_PASSWORD", DEFAULT_SNOW_PASSWORD)
    return instance, user, password


def snow_missing_config():
    instance, user, password = snow_cfg()
    missing = []
    if not instance:
        missing.append("SNOW_INSTANCE")
    if not user:
        missing.append("SNOW_USER")
    if not password:
        missing.append("SNOW_PASSWORD")
    return missing


def snow_base():
    instance, _, _ = snow_cfg()
    return f"https://{instance}/api/now"


def snow_auth():
    _, user, password = snow_cfg()
    return user, password


def snow_instance_host():
    instance, _, _ = snow_cfg()
    return instance


SNOW_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
}


def snow_check(response):
    if response.status_code < 400:
        return response

    detail = ""
    try:
        body = response.json()
        err = body.get("error") or {}
        detail = err.get("message") or err.get("detail") or json.dumps(body)
    except Exception:
        detail = (response.text or "")[:500]

    raise requests.HTTPError(
        f"HTTP {response.status_code} on {response.request.method} {response.url} -> {detail}"
    )


def snow_get(table, query, fields=None, limit=100, display_value=None):
    params = {
        "sysparm_query": query,
        "sysparm_limit": limit,
    }
    if fields:
        params["sysparm_fields"] = ",".join(fields)
    if display_value:
        params["sysparm_display_value"] = display_value

    response = requests.get(
        f"{snow_base()}/table/{table}",
        auth=snow_auth(),
        headers=SNOW_HEADERS,
        params=params,
        timeout=30,
    )
    snow_check(response)
    return response.json().get("result", [])


def snow_create(table, body):
    response = requests.post(
        f"{snow_base()}/table/{table}",
        auth=snow_auth(),
        headers=SNOW_HEADERS,
        data=json.dumps(body),
        timeout=30,
    )
    snow_check(response)
    return response.json()["result"]


def snow_update(table, sys_id, body):
    response = requests.patch(
        f"{snow_base()}/table/{table}/{sys_id}",
        auth=snow_auth(),
        headers=SNOW_HEADERS,
        data=json.dumps(body),
        timeout=30,
    )
    snow_check(response)
    return response.json()["result"]


def snow_delete(table, sys_id):
    response = requests.delete(
        f"{snow_base()}/table/{table}/{sys_id}",
        auth=snow_auth(),
        headers=SNOW_HEADERS,
        timeout=30,
    )
    if response.status_code not in (200, 204):
        snow_check(response)
    return True


def snow_attach(table, sys_id, file_name, content_bytes, content_type="text/markdown"):
    url = f"{snow_base()}/attachment/file"
    params = {
        "table_name": table,
        "table_sys_id": sys_id,
        "file_name": file_name,
    }
    response = requests.post(
        url,
        auth=snow_auth(),
        params=params,
        headers={
            "Content-Type": content_type,
            "Accept": "application/json",
        },
        data=content_bytes,
        timeout=30,
    )
    snow_check(response)
    return response.json().get("result", {})


def search_users(term="", limit=200):
    term = (term or "").strip()
    if term:
        query = f"active=true^nameLIKE{term}^ORemailLIKE{term}^ORuser_nameLIKE{term}^ORDERBYname"
    else:
        query = "active=true^ORDERBYname"

    rows = snow_get(
        "sys_user",
        query,
        fields=["sys_id", "name", "email", "user_name"],
        limit=limit,
    )
    users = []
    for row in rows:
        name = row.get("name") or row.get("user_name") or row.get("email") or row.get("sys_id")
        email = row.get("email") or ""
        users.append({
            "sys_id": row.get("sys_id"),
            "name": name,
            "email": email,
            "user_name": row.get("user_name", ""),
            "label": f"{name} ({email})" if email else name,
        })
    return users


def create_approval_record(approver_sys_id: str, ritm_sys_id: str):
    """
    Creates an approval record in sysapproval_approver linked to the RITM.
    Uses Scripted REST API with GlideRecord (per snow_rest_api_setup.md) to preserve
    Approver and SysApproval references, with Table API fallback.
    """
    # 1. Try Scripted REST API endpoint
    try:
        scripted_url = f"{snow_base().replace('/api/now', '')}/api/1986245/devops_user_helper/create_approval"
        resp = requests.post(
            scripted_url,
            auth=snow_auth(),
            headers=SNOW_HEADERS,
            json={"approver": approver_sys_id, "sysapproval": ritm_sys_id, "document_id": ritm_sys_id},
            timeout=30,
        )
        if resp.status_code == 200 and resp.json().get("result", {}).get("sys_id"):
            log.info(f"✅ Approval created via Scripted REST API: {resp.json()['result']['sys_id']}")
            return resp.json()["result"]
    except Exception as e:
        log.warning(f"Scripted REST approval creation fallback: {e}")

    # 2. Fallback to Table API
    payload = {
        "approver": approver_sys_id,
        "sysapproval": ritm_sys_id,
        "document_id": ritm_sys_id,
        "source_table": "sc_req_item",
        "state": "requested",
    }
    try:
        res = snow_create("sysapproval_approver", payload)
        return res
    except Exception as e:
        log.warning(f"Direct approval creation warning: {e}. Retrying with minimal payload...")
        res = snow_create("sysapproval_approver", {"approver": approver_sys_id, "sysapproval": ritm_sys_id})
        return res

