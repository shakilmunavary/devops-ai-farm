"""
AIOps ServiceNow Module
Provides Service Catalog creation, ServiceNow REST client helpers,
and Mistral-powered Autonomous Runbook Automation (RBA) engine.
"""

from .snow_client import (
    snow_cfg,
    snow_get,
    snow_create,
    snow_update,
    snow_delete,
    snow_attach,
    search_users,
    normalize_instance,
)
from .catalog_manager import (
    list_catalogs,
    create_catalog,
    delete_catalog,
    full_catalog_name,
    extract_catalog_meta,
    encode_catalog_description,
)
from .aiops_engine import (
    aiops_engine,
    aiops_engine as aiops_daemon,
    list_orders,
    execute_order_with_mistral,
)

__all__ = [
    "snow_cfg",
    "snow_get",
    "snow_create",
    "snow_update",
    "snow_delete",
    "snow_attach",
    "search_users",
    "normalize_instance",
    "list_catalogs",
    "create_catalog",
    "delete_catalog",
    "full_catalog_name",
    "extract_catalog_meta",
    "encode_catalog_description",
    "aiops_engine",
    "aiops_daemon",
    "list_orders",
    "execute_order_with_mistral",
]
