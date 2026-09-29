"""Public hook contracts extracted from the evaluated runtime (see licenses/)."""
from __future__ import annotations
import json, math, re
from typing import Any, Mapping
MAX_HOOK_TEXT_BYTES = MAX_INTERNAL_TOOL_RESULT_BYTES = MAX_FINAL_OUTPUT_BYTES = 48 * 1024


_INTERNAL_TOOL_NAME_RE = re.compile(r"^harness_[a-z][a-z0-9_]{0,55}$")

_SUBAGENT_NAME_RE = re.compile(r"^specialist_[a-z][a-z0-9_]{0,52}$")

_CREDENTIAL_VALUE_RE = re.compile(
    r"(?:\bsk-[A-Za-z0-9_-]{8,}|\bBearer\s+[A-Za-z0-9._~+/-]{8,}|"
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)",
    re.IGNORECASE,
)

_FORBIDDEN_EXACT_KEYS = {
    "task_role",
    "variant_role",
    "gold",
    "gold_answer",
    "gold_label",
    "ground_truth",
    "oracle",
    "evaluator",
    "evaluation",
    "judge",
    "verifier",
    "credential",
    "credentials",
    "secret",
    "secrets",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "authorization",
    "access_token",
    "refresh_token",
    "auth_token",
    "private_db",
    "private_database",
    "private_database_state",
    "database_snapshot",
    "db_snapshot",
    "hidden_state",
    "initial_state",
}

_FORBIDDEN_KEY_FRAGMENTS = (
    "gold_",
    "ground_truth",
    "evaluator",
    "credential",
    "private_db",
    "private_database",
)

class OpenHarnessRuntimeError(RuntimeError):
    """Base class for open-harness runtime failures."""

class OpenHarnessPublicDataError(OpenHarnessRuntimeError):
    """A request or response attempted to cross the public-data boundary."""

def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise OpenHarnessPublicDataError("value is not deterministic JSON") from exc

def _json_clone(value: Any) -> Any:
    return json.loads(_canonical_json_bytes(value).decode("utf-8"))

def _normalise_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")

def _is_public_access_metadata(key, value):
    # Only non-secret simulation metadata; arbitrary credentials remain denied.
    if key == "access_role":
        return type(value) is str and value in {"owner", "writer", "reader", "freeBusyReader"}
    if key == "credential_status":
        return type(value) is str and value in {"active", "inactive", "expired", "revoked"}
    if key in {"credential_ref", "current_credential_ref", "stored_credential_ref"}:
        return type(value) is str and re.fullmatch(r"cred_[a-z0-9_]{1,64}", value) is not None
    if key == "credentials":
        fields = {"credential_ref", "owner", "router_id", "scope", "status"}
        return type(value) is list and all(
            type(row) is dict and set(row) == fields
            and all(type(v) is str for v in row.values())
            and _is_public_access_metadata("credential_ref", row["credential_ref"])
            and _is_public_access_metadata("credential_status", row["status"])
            for row in value
        )
    return False

def _assert_public_json(value: Any, *, label: str) -> None:
    """Reject hidden labels, credentials, non-JSON data, and non-finite values."""

    def visit(item: Any, path: str) -> None:
        if item is None or isinstance(item, (bool, int)):
            return
        if isinstance(item, float):
            if not math.isfinite(item):
                raise OpenHarnessPublicDataError(
                    f"{label}{path} contains a non-finite number"
                )
            return
        if isinstance(item, str):
            if _CREDENTIAL_VALUE_RE.search(item):
                raise OpenHarnessPublicDataError(
                    f"{label}{path} resembles credential material"
                )
            return
        if isinstance(item, list):
            for index, child in enumerate(item):
                visit(child, f"{path}[{index}]")
            return
        if isinstance(item, dict):
            for raw_key, child in item.items():
                if not isinstance(raw_key, str):
                    raise OpenHarnessPublicDataError(
                        f"{label}{path} has a non-string key"
                    )
                key = _normalise_key(raw_key)
                if (
                    key in _FORBIDDEN_EXACT_KEYS
                    or key.endswith("_role")
                    or (
                        any(fragment in key for fragment in _FORBIDDEN_KEY_FRAGMENTS)
                        and not (raw_key == "admin_credential_dependency" and type(child) is bool)
                    )
                ) and not _is_public_access_metadata(raw_key, child):
                    raise OpenHarnessPublicDataError(
                        f"{label}{path}.{raw_key} is not part of the public runtime view"
                    )
                visit(child, f"{path}.{raw_key}")
            return
        raise OpenHarnessPublicDataError(
            f"{label}{path} has non-JSON type {type(item).__name__}"
        )

    visit(value, "")
    _canonical_json_bytes(value)

def _validate_state(state: Any, max_state_bytes: int) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise ValueError("state must be a JSON object")
    _assert_public_json(state, label="component_state")
    encoded = _canonical_json_bytes(state)
    if len(encoded) > max_state_bytes:
        raise OverflowError("component state exceeds max_state_bytes")
    return json.loads(encoded.decode("utf-8"))

def _validate_text(
    value: Any,
    *,
    label: str,
    maximum: int,
    allow_empty: bool = True,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    if not allow_empty and not value.strip():
        raise ValueError(f"{label} must be non-empty")
    if len(value.encode("utf-8")) > maximum:
        raise ValueError(f"{label} exceeds {maximum} UTF-8 bytes")
    return value

def _validate_hook_result(
    hook: str,
    raw: Any,
    *,
    max_state_bytes: int,
    draft_output: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if hook == "initialize":
        state = _validate_state(raw, max_state_bytes)
        return {"state": state}, state
    if not isinstance(raw, dict):
        raise ValueError("hook result must be a JSON object")
    expected_keys = {
        "configure_runtime": {"internal_tools", "subagents", "state"},
        "compose_instructions": {"additional_instructions", "state"},
        "before_tool_call": {"decision", "message", "state"},
        "after_tool_result": {"guidance", "state"},
        "invoke_internal_tool": {"content", "state"},
        "finalize_output": {"decision", "output", "reason", "state"},
    }[hook]
    if set(raw) != expected_keys:
        raise ValueError("hook result keys do not match the open component ABI")
    state = _validate_state(raw["state"], max_state_bytes)
    result = dict(raw)
    result["state"] = state
    if hook == "configure_runtime":
        descriptors = _validate_runtime_descriptors(
            {
                "internal_tools": result["internal_tools"],
                "subagents": result["subagents"],
            }
        )
        result.update(descriptors)
    elif hook == "compose_instructions":
        _validate_text(
            result["additional_instructions"],
            label="additional_instructions",
            maximum=MAX_HOOK_TEXT_BYTES,
        )
    elif hook == "before_tool_call":
        if result["decision"] not in {"allow", "request_replan"}:
            raise ValueError("decision must be allow or request_replan")
        _validate_text(
            result["message"], label="message", maximum=MAX_HOOK_TEXT_BYTES
        )
    elif hook == "after_tool_result":
        _validate_text(
            result["guidance"], label="guidance", maximum=MAX_HOOK_TEXT_BYTES
        )
    elif hook == "invoke_internal_tool":
        _validate_text(
            result["content"],
            label="internal tool content",
            maximum=MAX_INTERNAL_TOOL_RESULT_BYTES,
        )
    elif hook == "finalize_output":
        decision = result["decision"]
        if decision not in {"accept", "replace", "abstain", "revise"}:
            raise ValueError("final decision must be accept, replace, abstain, or revise")
        reason = _validate_text(
            result["reason"],
            label="final reason",
            maximum=MAX_HOOK_TEXT_BYTES,
            allow_empty=decision == "accept",
        )
        if decision in {"accept", "revise"}:
            if result["output"] is not None:
                raise ValueError("accept decision requires null output")
        else:
            _validate_text(
                result["output"],
                label="final output",
                maximum=MAX_FINAL_OUTPUT_BYTES,
                allow_empty=False,
            )
            if not reason.strip():
                raise ValueError("replace and abstain require a non-empty reason")
    _assert_public_json(result, label=f"{hook}_result")
    return _json_clone(result), state

def _component_context(
    base_context: Mapping[str, Any],
    middleware: Mapping[str, Any],
    state: Mapping[str, Any],
) -> dict[str, Any]:
    context = _json_clone(dict(base_context))
    reserved = {
        "state",
        "base_instructions",
        "tool_name",
        "arguments",
        "result",
        "draft_output",
        "middleware_id",
        "middleware_params",
    }
    if reserved.intersection(context):
        raise ValueError("context uses a runtime-reserved key")
    context["state"] = _json_clone(dict(state))
    context["middleware_id"] = middleware["id"]
    context["middleware_params"] = _json_clone(middleware["params"])
    return context

def _validate_runtime_descriptors(raw: Any) -> dict[str, list[dict[str, str]]]:
    if not isinstance(raw, dict) or set(raw) != {"internal_tools", "subagents"}:
        raise ValueError("runtime descriptors have an invalid shape")
    tools = raw["internal_tools"]
    subagents = raw["subagents"]
    if not isinstance(tools, list) or not isinstance(subagents, list):
        raise ValueError("runtime descriptors must be lists")
    normalized_tools: list[dict[str, str]] = []
    for item in tools:
        if not isinstance(item, dict) or set(item) != {"name", "description"}:
            raise ValueError("internal tool descriptor has an invalid shape")
        name = item["name"]
        if not isinstance(name, str) or _INTERNAL_TOOL_NAME_RE.fullmatch(name) is None:
            raise ValueError("internal tool name is invalid")
        normalized_tools.append(
            {
                "name": name,
                "description": _validate_text(
                    item["description"],
                    label="internal tool description",
                    maximum=48 * 1024,
                    allow_empty=False,
                ),
            }
        )
    if len({item["name"] for item in normalized_tools}) != len(normalized_tools):
        raise ValueError("internal tool names are duplicated")
    normalized_subagents: list[dict[str, str]] = []
    for item in subagents:
        if not isinstance(item, dict) or set(item) != {
            "name",
            "description",
            "instructions",
        }:
            raise ValueError("subagent descriptor has an invalid shape")
        name = item["name"]
        if not isinstance(name, str) or _SUBAGENT_NAME_RE.fullmatch(name) is None:
            raise ValueError("subagent name is invalid")
        normalized_subagents.append(
            {
                "name": name,
                "description": _validate_text(
                    item["description"],
                    label="subagent description",
                    maximum=48 * 1024,
                    allow_empty=False,
                ),
                "instructions": _validate_text(
                    item["instructions"],
                    label="subagent instructions",
                    maximum=48 * 1024,
                    allow_empty=False,
                ),
            }
        )
    if len({item["name"] for item in normalized_subagents}) != len(normalized_subagents):
        raise ValueError("subagent names are duplicated")
    return {
        "internal_tools": normalized_tools,
        "subagents": normalized_subagents,
    }
