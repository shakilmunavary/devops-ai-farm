#!/usr/bin/env python3
"""
catalog_manager.py
Service Catalog Item lifecycle management in ServiceNow:
- Catalog item creation (sc_cat_item) with clean modern HTML rendering & approver badge
- Dynamic variable fields (item_option_new)
- Question choices for dropdowns (question_choice / question_choices)
- Catalog client scripts to lock variables and dropdowns on RITM and sc_task views
- Catalog UI Policies and UI Policy Actions to ensure complete read-only locking on RITM views
- Metadata encoding & decoding for approvals
- Cascading deletion of catalogs, variables, client scripts, and UI policies
"""

import re
import json
import html
import logging
from typing import Dict, Any, List, Tuple, Optional

from .snow_client import (
    snow_get,
    snow_create,
    snow_update,
    snow_delete,
    snow_attach,
    snow_cfg,
    snow_instance_host,
)

log = logging.getLogger("aiops-snow")

SOP_AGENT_PREFIX = "AIOPS-"
LEGACY_PREFIX = "AIOPS-SOP-"
AWS_LEGACY_PREFIX = "AWS-SOP-Agent-"
CATALOG_TITLE = "Service Catalog"

META_MARKER = "AIOPS_SOP_META="

TYPE_TEXT = "6"
TYPE_SELECT_BOX = "5"

_CHOICES_TABLE = "question_choice"
_CHOICES_FALLBACKS = ["question_choice", "question_choices", "sc_item_option_choice"]


def slug(name: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", (name or "").strip())
    text = text.strip("_").lower()
    return text or "field"


def full_catalog_name(short_name: str) -> str:
    short_name = (short_name or "").strip()
    if short_name.startswith(SOP_AGENT_PREFIX) or short_name.startswith(LEGACY_PREFIX) or short_name.startswith(AWS_LEGACY_PREFIX):
        return short_name
    return f"{SOP_AGENT_PREFIX}{short_name}"


def choices_table() -> str:
    global _CHOICES_TABLE
    if _CHOICES_TABLE:
        return _CHOICES_TABLE

    candidates = []
    try:
        rows = snow_get("sys_db_object", "nameLIKEchoice", ["name"], 100)
        candidates = [row["name"] for row in rows if row.get("name")]
    except Exception:
        candidates = []

    for name in candidates:
        try:
            rows = snow_get("sys_dictionary", f"name={name}^element=question", ["element"], 1)
            if rows:
                _CHOICES_TABLE = name
                return name
        except Exception:
            pass

    for fallback in _CHOICES_FALLBACKS:
        if fallback in candidates:
            _CHOICES_TABLE = fallback
            return fallback

    _CHOICES_TABLE = candidates[0] if candidates else "question_choices"
    return _CHOICES_TABLE


def resolve_catalog_and_category(category_title: str) -> Tuple[str, str]:
    catalog = snow_get("sc_catalog", f"title={CATALOG_TITLE}", ["sys_id"], 1)
    catalog_id = catalog[0]["sys_id"] if catalog else ""

    category_query = f"title={category_title}"
    if catalog_id:
        category_query += f"^sc_catalog={catalog_id}"

    existing = snow_get("sc_category", category_query, ["sys_id"], 1)
    if existing:
        return catalog_id, existing[0]["sys_id"]

    body = {"title": category_title, "active": "true"}
    if catalog_id:
        body["sc_catalog"] = catalog_id

    category_id = snow_create("sc_category", body)["sys_id"]
    return catalog_id, category_id


def markdown_to_clean_html(sop_markdown: str, short_desc: str = "", meta: Optional[Dict[str, Any]] = None) -> str:
    """Converts raw markdown into a clean, modern, readable HTML card for ServiceNow Service Catalog."""
    if not sop_markdown:
        return f"<div style='font-family: -apple-system, sans-serif; color: #475569; padding: 10px;'>{html.escape(short_desc or 'Autonomous AIOps Runbook')}</div>"

    if sop_markdown.strip().startswith("<div") and "style=" in sop_markdown and not meta:
        return sop_markdown

    # If markdown was already rendered HTML, strip wrapper to re-render cleanly with meta
    if sop_markdown.strip().startswith("<div"):
        # Strip old outer div or keep clean text
        pass

    lines = sop_markdown.strip().splitlines()
    title = ""
    sections = []
    current_sec = {"heading": "", "lines": []}

    for line in lines:
        l = line.strip()
        if l.startswith("# "):
            title = l.lstrip("# ").strip()
        elif l.startswith("## ") or l.startswith("### "):
            if current_sec["heading"] or current_sec["lines"]:
                sections.append(current_sec)
            current_sec = {"heading": l.lstrip("# ").strip(), "lines": []}
        elif l.startswith("---") or l.startswith("***"):
            continue
        else:
            if l:
                current_sec["lines"].append(l)

    if current_sec["heading"] or current_sec["lines"]:
        sections.append(current_sec)

    approver_badge = ""
    governance_card = ""
    if meta:
        req = meta.get("approval_required")
        approver = meta.get("approver_name") or "Designated Approver"
        if req:
            approver_badge = f'<span style="background: #fef3c7; color: #b45309; border: 1px solid #fde68a; font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 12px; margin-right: 6px;">👤 Approver: {html.escape(approver)}</span>'
            governance_card = (
                f'<div style="background: #fffbeb; border: 1px solid #fde68a; border-left: 4px solid #f59e0b; border-radius: 6px; padding: 10px 14px; margin-bottom: 14px; display: flex; align-items: center; justify-content: space-between;">'
                f'<div>'
                f'<span style="font-size: 11px; font-weight: 700; color: #b45309; text-transform: uppercase; letter-spacing: 0.5px;">Governance & Approvals</span>'
                f'<div style="font-size: 13px; font-weight: 600; color: #78350f; margin-top: 2px;">👤 Designated Approver: <strong>{html.escape(approver)}</strong> <span style="font-size: 11px; color: #92400e; font-weight: normal;">(Requires sign-off before automated execution)</span></div>'
                f'</div>'
                f'<span style="background: #fef3c7; color: #b45309; border: 1px solid #fde68a; font-size: 11px; font-weight: 700; padding: 3px 8px; border-radius: 4px;">Approval Required</span>'
                f'</div>'
            )
        else:
            governance_card = (
                f'<div style="background: #f0fdf4; border: 1px solid #bbf7d0; border-left: 4px solid #22c55e; border-radius: 6px; padding: 8px 14px; margin-bottom: 14px; display: flex; align-items: center; justify-content: space-between;">'
                f'<div>'
                f'<span style="font-size: 11px; font-weight: 700; color: #166534; text-transform: uppercase; letter-spacing: 0.5px;">Governance & Approvals</span>'
                f'<div style="font-size: 13px; font-weight: 600; color: #14532d; margin-top: 2px;">⚡ Direct Autonomous Dispatch <span style="font-size: 11px; color: #166534; font-weight: normal;">(Pre-approved for automated cloud execution)</span></div>'
                f'</div>'
                f'<span style="background: #dcfce7; color: #166534; border: 1px solid #86efac; font-size: 11px; font-weight: 700; padding: 3px 8px; border-radius: 4px;">Auto-Approved</span>'
                f'</div>'
            )

    html_parts = [
        '<div style="font-family: -apple-system, BlinkMacSystemFont, Arial, sans-serif; font-size: 13px; line-height: 1.6; color: #334155; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 10px; padding: 18px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">'
    ]

    header_title = title or short_desc or "AIOps Autonomous Runbook"
    html_parts.append(
        f'<div style="display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid #f1f5f9; padding-bottom: 10px; margin-bottom: 14px;">'
        f'<span style="font-size: 15px; font-weight: 700; color: #0f172a;">⚡ {html.escape(header_title)}</span>'
        f'<div style="display: flex; align-items: center;">'
        f'{approver_badge}'
        f'<span style="background: #e0f2fe; color: #0284c7; border: 1px solid #bae6fd; font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 12px;">AIOps Verified</span>'
        f'</div>'
        f'</div>'
    )

    if governance_card:
        html_parts.append(governance_card)

    for sec in sections:
        h = sec["heading"]
        content_lines = sec["lines"]
        if not h and not content_lines:
            continue

        if h:
            html_parts.append(f'<div style="margin-bottom: 12px;"><div style="font-weight: 700; color: #0f172a; margin-bottom: 4px;">{html.escape(h)}</div>')
        else:
            html_parts.append('<div style="margin-bottom: 12px;">')

        in_list = False
        for cl in content_lines:
            cl_strip = cl.strip()
            if cl_strip.startswith("- ") or cl_strip.startswith("* "):
                if not in_list:
                    html_parts.append('<ul style="margin: 0; padding-left: 20px; color: #475569;">')
                    in_list = True
                item_txt = html.escape(cl_strip[2:])
                item_txt = re.sub(r"`([^`]+)`", r'<code style="background: #f1f5f9; padding: 2px 5px; border-radius: 4px; font-size: 12px; color: #0284c7;">\1</code>', item_txt)
                item_txt = re.sub(r"\*\*([^*]+)\*\*", r'<strong>\1</strong>', item_txt)
                html_parts.append(f'<li>{item_txt}</li>')
            elif cl_strip.startswith("```"):
                if in_list:
                    html_parts.append('</ul>')
                    in_list = False
                continue
            elif "az vm " in cl_strip or "aws ec2 " in cl_strip or "POST " in cl_strip:
                if in_list:
                    html_parts.append('</ul>')
                    in_list = False
                html_parts.append(f'<div style="background: #0f172a; color: #38bdf8; font-family: monospace; font-size: 12px; padding: 6px 10px; border-radius: 6px; margin: 4px 0;">{html.escape(cl_strip)}</div>')
            else:
                if in_list:
                    html_parts.append('</ul>')
                    in_list = False
                p_txt = html.escape(cl_strip)
                p_txt = re.sub(r"`([^`]+)`", r'<code style="background: #f1f5f9; padding: 2px 5px; border-radius: 4px; font-size: 12px; color: #0284c7;">\1</code>', p_txt)
                p_txt = re.sub(r"\*\*([^*]+)\*\*", r'<strong>\1</strong>', p_txt)
                html_parts.append(f'<div style="color: #475569; margin-bottom: 4px;">{p_txt}</div>')

        if in_list:
            html_parts.append('</ul>')
        html_parts.append('</div>')

    html_parts.append('</div>')
    return "\n".join(html_parts)


def encode_catalog_description(sop_markdown: str, meta: Dict[str, Any], short_desc: str = "") -> str:
    html_desc = markdown_to_clean_html(sop_markdown or "", short_desc, meta=meta)
    meta_json = json.dumps(meta or {}, separators=(",", ":"))
    return f"{html_desc}\n\n<span style=\"display: none;\">{META_MARKER}{meta_json}</span>"


def extract_catalog_meta(description: str) -> Tuple[Dict[str, Any], str]:
    description = html.unescape(description or "")
    default_meta = {
        "approval_required": False,
        "approver_sys_id": "",
        "approver_name": "",
    }

    if META_MARKER not in description and "AWS_SOP_META=" not in description:
        return default_meta, description

    marker = META_MARKER if META_MARKER in description else "AWS_SOP_META="
    try:
        idx = description.rfind(marker)
        raw = description[idx + len(marker):].strip()
        m = re.search(r"(\{[\s\S]*?\})", raw)
        if m:
            meta = json.loads(m.group(1))
            default_meta.update(meta)
        sop = description[:idx].strip()
        sop = re.sub(r"<[^>]+$", "", sop).strip()
        return default_meta, sop
    except Exception as exc:
        log.warning(f"Meta parse error: {exc}")
        return default_meta, description


def create_readonly_client_script(item_id: str, item_name: str, variable_names: List[str]):
    """Creates/Updates Catalog Client Script to lock all variables and dropdowns on RITM and sc_task views."""
    if not variable_names:
        return None

    js_names = json.dumps(variable_names)
    script = f"""function onLoad() {{
  var vars = {js_names};
  for (var i = 0; i < vars.length; i++) {{
    var v = vars[i];
    try {{
      g_form.setReadOnly(v, true);
      g_form.setReadOnly('variables.' + v, true);
      g_form.setDisabled(v, true);
      g_form.setDisabled('variables.' + v, true);
    }} catch (e) {{}}
    try {{
      var ctrl = g_form.getControl(v) || g_form.getControl('variables.' + v);
      if (ctrl) {{
        ctrl.disabled = true;
        ctrl.setAttribute('disabled', 'disabled');
        ctrl.setAttribute('readonly', 'readonly');
        ctrl.style.pointerEvents = 'none';
        ctrl.style.backgroundColor = '#e9ecef';
        ctrl.style.cursor = 'not-allowed';
      }}
    }} catch (e) {{}}
    try {{
      var els = document.querySelectorAll('select[name*="' + v + '"], select[id*="' + v + '"], input[name*="' + v + '"], textarea[name*="' + v + '"]');
      for (var k = 0; k < els.length; k++) {{
        els[k].disabled = true;
        els[k].setAttribute('disabled', 'disabled');
        els[k].setAttribute('readonly', 'readonly');
        els[k].style.pointerEvents = 'none';
        els[k].style.backgroundColor = '#e9ecef';
        els[k].style.cursor = 'not-allowed';
      }}
    }} catch (e) {{}}
  }}
}}""".strip()

    candidate_bodies = [
        {
            "cat_item": item_id,
            "name": f"{item_name} - lock variables on RITM",
            "type": "onLoad",
            "script": script,
            "active": "true",
            "applies_catalog": "false",
            "applies_req_item": "true",
            "applies_sc_task": "true",
            "applies_target_record": "true",
        },
        {
            "cat_item": item_id,
            "name": f"{item_name} - lock variables on RITM",
            "type": "onLoad",
            "script": script,
            "active": "true",
            "applies_req_item": "true",
            "applies_sc_task": "true",
        }
    ]
    last_error = None
    for body in candidate_bodies:
        try:
            return snow_create("catalog_script_client", body)
        except Exception as exc:
            last_error = exc
    log.warning(f"Could not create client script: {last_error}")
    return None


def create_catalog_ui_policy(item_id: str, item_name: str, variable_sys_ids: Dict[str, str]):
    """Creates a Catalog UI Policy and actions to ensure all variables and dropdowns are disabled on RITM and sc_task."""
    if not variable_sys_ids:
        return None

    policy_body = {
        "catalog_item": item_id,
        "applies_to": "item",
        "short_description": f"{item_name} - lock variables on RITM/Task",
        "applies_catalog": "false",
        "applies_req_item": "true",
        "applies_sc_task": "true",
        "applies_target_record": "true",
        "active": "true",
        "on_load": "true",
        "reverse_if_false": "false",
    }
    try:
        policy_res = snow_create("catalog_ui_policy", policy_body)
        policy_id = policy_res.get("sys_id")
        if not policy_id:
            return None

        for v_name, v_sys_id in variable_sys_ids.items():
            action_body = {
                "ui_policy": policy_id,
                "catalog_variable": f"IO:{v_sys_id}",
                "catalog_item": item_id,
                "disabled": "true",
                "mandatory": "ignore",
                "visible": "ignore",
            }
            try:
                snow_create("catalog_ui_policy_action", action_body)
            except Exception as e:
                log.warning(f"Could not create UI policy action for {v_name}: {e}")
        return policy_id
    except Exception as exc:
        log.warning(f"Could not create catalog UI policy: {exc}")
        return None


def list_catalogs() -> List[Dict[str, Any]]:
    query = f"nameSTARTSWITH{SOP_AGENT_PREFIX}^ORnameSTARTSWITH{LEGACY_PREFIX}^ORnameSTARTSWITH{AWS_LEGACY_PREFIX}"
    rows = snow_get(
        "sc_cat_item",
        query,
        fields=["sys_id", "name", "short_description", "active", "description", "sys_created_on"],
        limit=1000,
    )
    output = []
    for row in rows:
        variable_rows = snow_get(
            "item_option_new",
            f"cat_item={row['sys_id']}",
            fields=["name", "type", "question_text", "mandatory"],
            limit=100,
        )
        meta, sop = extract_catalog_meta(row.get("description", ""))
        
        fields = []
        for var in variable_rows:
            var_type_code = str(var.get("type"))
            fields.append({
                "name": var.get("name"),
                "label": var.get("question_text") or var.get("name"),
                "type": "dropdown" if var_type_code == TYPE_SELECT_BOX else "text",
                "mandatory": var.get("mandatory") == "true",
            })

        host = snow_instance_host()
        order_url = f"https://{host}/nav_to.do?uri=com.glideapp.servicecatalog_cat_item_view.do?sysparm_id={row['sys_id']}" if host else ""

        output.append({
            "sys_id": row["sys_id"],
            "name": row["name"],
            "short_description": row.get("short_description", ""),
            "active": row.get("active", "true"),
            "approval_required": bool(meta.get("approval_required")),
            "approver_sys_id": meta.get("approver_sys_id", ""),
            "approver_name": meta.get("approver_name", ""),
            "sop_markdown": sop,
            "fields": fields,
            "created_on": row.get("sys_created_on", ""),
            "order_url": order_url,
        })

    output.sort(key=lambda x: x["name"])
    return output


def create_catalog(
    short_name: str,
    short_description: str,
    sop_markdown: str,
    fields: List[Dict[str, Any]],
    approval_required: bool = False,
    approver_sys_id: str = "",
    approver_name: str = "",
    category_name: str = "AIOps Runbooks",
) -> Dict[str, Any]:
    name = full_catalog_name(short_name)

    existing = snow_get("sc_cat_item", f"name={name}", fields=["sys_id"], limit=1)
    if existing:
        raise ValueError(f"A catalog item named '{name}' already exists. Delete it first before re-creating.")

    if approval_required and not approver_sys_id:
        raise ValueError("Approver is required when 'Approval Required' is enabled.")

    catalog_id, category_id = resolve_catalog_and_category(category_name or "AIOps Runbooks")

    meta = {
        "approval_required": bool(approval_required),
        "approver_sys_id": approver_sys_id or "",
        "approver_name": approver_name or "",
    }

    item_body = {
        "name": name,
        "short_description": short_description or name,
        "description": encode_catalog_description(sop_markdown or "", meta, short_description),
        "active": "true",
        "billable": "false",
    }
    if category_id:
        item_body["category"] = category_id
    if catalog_id:
        item_body["sc_catalogs"] = catalog_id

    item_id = snow_create("sc_cat_item", item_body)["sys_id"]

    try:
        snow_attach("sc_cat_item", item_id, "SOP_Runbook.md", sop_markdown or "# SOP Runbook")
    except Exception as e:
        log.warning(f"Could not attach SOP markdown: {e}")

    warnings = []
    order = 100
    created_variable_names = []
    created_var_sys_ids = {}

    for field in fields:
        label = (field.get("label") or "").strip()
        if not label:
            continue

        field_type = field.get("type", "text")
        is_dropdown = field_type == "dropdown"
        type_code = TYPE_SELECT_BOX if is_dropdown else TYPE_TEXT
        field_name = slug(field.get("name") or label)

        var_body = {
            "cat_item": item_id,
            "name": field_name,
            "question_text": label,
            "type": type_code,
            "order": str(order),
            "mandatory": "true" if field.get("mandatory", True) else "false",
            "active": "true",
        }
        var_id = snow_create("item_option_new", var_body)["sys_id"]
        created_variable_names.append(field_name)
        created_var_sys_ids[field_name] = var_id

        if is_dropdown:
            choices = field.get("choices") or []
            choice_table = choices_table()
            choice_order = 100
            for ch in choices:
                if isinstance(ch, dict):
                    c_text = ch.get("text") or ch.get("label") or ""
                    c_val = ch.get("value") or c_text
                else:
                    c_text = str(ch)
                    c_val = str(ch)
                if not c_text:
                    continue

                ch_body = {
                    "question": var_id,
                    "text": c_text,
                    "value": c_val,
                    "order": str(choice_order),
                    "inactive": "false",
                }
                try:
                    snow_create(choice_table, ch_body)
                except Exception as exc:
                    warnings.append(f"Failed to create choice '{c_text}': {exc}")
                choice_order += 100

        order += 100

    create_readonly_client_script(item_id, name, created_variable_names)
    create_catalog_ui_policy(item_id, name, created_var_sys_ids)

    host = snow_instance_host()
    order_url = f"https://{host}/nav_to.do?uri=com.glideapp.servicecatalog_cat_item_view.do?sysparm_id={item_id}" if host else ""

    return {
        "sys_id": item_id,
        "name": name,
        "order_url": order_url,
        "warnings": warnings,
    }


def delete_catalog(catalog_sys_id: str) -> Dict[str, Any]:
    items = snow_get("sc_cat_item", f"sys_id={catalog_sys_id}", ["name"], 1)
    if not items:
        return {"deleted": False, "reason": "Catalog item not found"}

    item_name = items[0].get("name", "Unknown")

    vars_rows = snow_get("item_option_new", f"cat_item={catalog_sys_id}", ["sys_id"], 100)
    choice_tbl = choices_table()
    for v in vars_rows:
        v_id = v["sys_id"]
        try:
            ch_rows = snow_get(choice_tbl, f"question={v_id}", ["sys_id"], 100)
            for ch in ch_rows:
                snow_delete(choice_tbl, ch["sys_id"])
        except Exception:
            pass
        snow_delete("item_option_new", v_id)

    scripts = snow_get("catalog_script_client", f"cat_item={catalog_sys_id}", ["sys_id"], 20)
    for s in scripts:
        try:
            snow_delete("catalog_script_client", s["sys_id"])
        except Exception:
            pass

    policies = snow_get("catalog_ui_policy", f"catalog_item={catalog_sys_id}", ["sys_id"], 20)
    for p in policies:
        p_id = p["sys_id"]
        try:
            p_actions = snow_get("catalog_ui_policy_action", f"ui_policy={p_id}", ["sys_id"], 50)
            for pa in p_actions:
                snow_delete("catalog_ui_policy_action", pa["sys_id"])
        except Exception:
            pass
        try:
            snow_delete("catalog_ui_policy", p_id)
        except Exception:
            pass

    snow_delete("sc_cat_item", catalog_sys_id)
    log.info(f"🗑️ Deleted catalog '{item_name}' ({catalog_sys_id})")
    return {"deleted": True, "name": item_name}


def sync_all_existing_catalogs() -> List[Dict[str, Any]]:
    """Synchronizes all existing AIOPS catalog items with the latest description format, approver badges, client scripts, and UI policies."""
    query = f"nameSTARTSWITH{SOP_AGENT_PREFIX}^ORnameSTARTSWITH{LEGACY_PREFIX}^ORnameSTARTSWITH{AWS_LEGACY_PREFIX}"
    items = snow_get("sc_cat_item", query, ["sys_id", "name", "description", "short_description"], 1000)
    results = []

    for item in items:
        item_id = item["sys_id"]
        item_name = item.get("name", "Unknown")
        short_desc = item.get("short_description", "")
        meta, sop = extract_catalog_meta(item.get("description", ""))

        # 1. Update Description with Clean Card and Approver Badges
        updated_desc = encode_catalog_description(sop or "", meta, short_desc)
        snow_update("sc_cat_item", item_id, {"description": updated_desc})

        # 2. Get Variables
        vars_rows = snow_get("item_option_new", f"cat_item={item_id}", ["sys_id", "name"], 100)
        var_names = [v["name"] for v in vars_rows if v.get("name")]
        var_sys_ids = {v["name"]: v["sys_id"] for v in vars_rows if v.get("name")}

        # 3. Clean and Recreate Client Scripts
        old_scripts = snow_get("catalog_script_client", f"cat_item={item_id}", ["sys_id"], 20)
        for s in old_scripts:
            try:
                snow_delete("catalog_script_client", s["sys_id"])
            except Exception:
                pass
        create_readonly_client_script(item_id, item_name, var_names)

        # 4. Clean and Recreate UI Policies
        old_policies = snow_get("catalog_ui_policy", f"catalog_item={item_id}", ["sys_id"], 20)
        for p in old_policies:
            p_id = p["sys_id"]
            try:
                p_actions = snow_get("catalog_ui_policy_action", f"ui_policy={p_id}", ["sys_id"], 50)
                for pa in p_actions:
                    snow_delete("catalog_ui_policy_action", pa["sys_id"])
            except Exception:
                pass
            try:
                snow_delete("catalog_ui_policy", p_id)
            except Exception:
                pass
        create_catalog_ui_policy(item_id, item_name, var_sys_ids)

        results.append({
            "sys_id": item_id,
            "name": item_name,
            "approver": meta.get("approver_name"),
            "approval_required": meta.get("approval_required"),
            "variables_locked": len(var_names)
        })

    return results
