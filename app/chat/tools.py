import inspect
import logging
import traceback

import httpx

logger = logging.getLogger(__name__)


def build_tools_from_config(tool_configs: list) -> list:
    """Build ADK-compatible tool functions from DB tool configurations."""
    tools = []
    for config in tool_configs:
        if not isinstance(config, dict):
            continue
        tool_type = config.get("type", "")
        if tool_type == "webhook":
            tools.append(_create_webhook_tool(config))
        elif tool_type == "rest_api":
            tools.append(_create_rest_api_tool(config))
    return tools


def _create_webhook_tool(config: dict):
    url = config["url"]
    method = config.get("method", "POST").upper()
    body_template = config.get("body", {})
    name = config.get("name", "webhook_tool")
    description = config.get("description", f"Call {name}")

    async def tool_fn(user_message: str) -> str:
        """Placeholder docstring — replaced below."""
        body = {}
        for key, value in body_template.items():
            if isinstance(value, str) and "{{message}}" in value:
                body[key] = value.replace("{{message}}", user_message)
            else:
                body[key] = value

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                if method == "POST":
                    response = await client.post(url, json=body)
                else:
                    response = await client.get(url, params=body)

            result = response.text
            if not result or not result.strip():
                return "The webhook returned an empty response. No data was found."
            return result
        except Exception as e:
            logger.error("Tool %s failed: %s\n%s", name, e, traceback.format_exc())
            return f"Error calling tool: {e}"

    # ADK inspects __name__ and __doc__ to build the tool schema for the LLM
    tool_fn.__name__ = name
    tool_fn.__doc__ = description

    return tool_fn


def _create_rest_api_tool(config: dict):
    """Create a tool with structured parameters that the LLM fills individually.

    Config format:
        {
            "type": "rest_api",
            "name": "create_ticket",
            "description": "Create a support ticket ...",
            "url": "https://example.com/api/tickets",
            "method": "POST",
            "headers": {"x-business-id": "gavigans"},
            "parameters": [
                {"name": "title", "type": "string", "description": "...", "required": true},
                {"name": "priority", "type": "string", "description": "...", "default": "medium"},
                {"name": "tags", "type": "list", "description": "Comma-separated tags"}
            ]
        }

    ADK reads __signature__ and __doc__ (Google-style Args) to present
    each parameter to the LLM as a callable function.
    """
    url = config["url"]
    method = config.get("method", "POST").upper()
    headers = config.get("headers", {})
    param_configs = config.get("parameters", [])
    name = config.get("name", "api_tool")
    description = config.get("description", f"Call {name}")

    # Track which params should be converted to lists
    list_params = {p["name"] for p in param_configs if p.get("type") == "list"}

    async def tool_fn(**kwargs) -> str:
        """placeholder"""
        body = {}
        for k, v in kwargs.items():
            if not v:
                continue
            if k in list_params and isinstance(v, str):
                body[k] = [item.strip() for item in v.split(",") if item.strip()]
            else:
                body[k] = v

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.request(method, url, json=body, headers=headers)

            result = response.text
            if not result or not result.strip():
                return "The API returned an empty response."
            return result
        except Exception as e:
            logger.error("Tool %s failed: %s\n%s", name, e, traceback.format_exc())
            return f"Error calling tool: {e}"

    # Build inspect.Signature so ADK sees named parameters
    params = []
    for p in param_configs:
        default = (
            inspect.Parameter.empty if p.get("required") else p.get("default", "")
        )
        params.append(
            inspect.Parameter(
                p["name"],
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=default,
                annotation=str,
            )
        )

    tool_fn.__signature__ = inspect.Signature(params, return_annotation=str)
    tool_fn.__name__ = name

    # Build Google-style docstring so ADK picks up parameter descriptions
    doc_lines = [description, "", "Args:"]
    for p in param_configs:
        doc_lines.append(f"    {p['name']}: {p.get('description', p['name'])}")
    tool_fn.__doc__ = "\n".join(doc_lines)

    return tool_fn
