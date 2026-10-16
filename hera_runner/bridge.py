from __future__ import annotations
import json
from typing import Any, Callable
from agents import FunctionTool, RunHooks
from mcp.types import CallToolResult, TextContent
from .harness import HarnessError
HarnessRuntimeError = OpenHarnessRuntimeError = HarnessError
_HARNESS_RUNTIME_ERRORS = (HarnessError,)
_CREDENTIAL_LIKE_TOOL_ARGUMENT_KEYS = frozenset({'access_token', 'api_key', 'apikey', 'authorization', 'auth_token', 'credential', 'credentials', 'password', 'passwd', 'refresh_token', 'secret', 'secrets'})
HARNESS_FAIL_CLOSED_INSTRUCTION = 'The executable harness failed at a trusted boundary. Do not call tools, claim completion, or infer missing evidence. Give a concise abstention that states the task could not be completed reliably.'

def coerce_final_output(value):
    if isinstance(value, str):
        return value
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)

class _CompletedModelUsageHooks(RunHooks):

    def __init__(self) -> None:
        self._responses: list[dict[str, int]] = []
        self._model_call_starts = 0

    async def on_llm_start(self, context: Any, agent: Any, system_prompt: str | None, input_items: list[Any]) -> None:
        del context, agent, system_prompt, input_items
        self._model_call_starts += 1

    async def on_llm_end(self, context: Any, agent: Any, response: Any) -> None:
        del context, agent
        usage = getattr(response, 'usage', None)
        if usage is None:
            return
        input_tokens = int(getattr(usage, 'input_tokens', 0) or 0)
        output_tokens = int(getattr(usage, 'output_tokens', 0) or 0)
        total_tokens = int(getattr(usage, 'total_tokens', 0) or 0)
        requests = int(getattr(usage, 'requests', 0) or 1)
        self._responses.append({'input_tokens': input_tokens, 'output_tokens': output_tokens, 'total_tokens': total_tokens or input_tokens + output_tokens, 'requests': requests})

    def metadata(self) -> dict[str, Any] | None:
        if not self._responses:
            return None
        return {'input_tokens': sum((item['input_tokens'] for item in self._responses)), 'output_tokens': sum((item['output_tokens'] for item in self._responses)), 'total_tokens': sum((item['total_tokens'] for item in self._responses)), 'requests': sum((item['requests'] for item in self._responses)), 'num_responses': len(self._responses), 'accounting_scope': 'completed_main_model_responses_only', 'request_usage': [dict(item) for item in self._responses]}

    @property
    def model_call_starts(self) -> int:
        return self._model_call_starts

def _compose_program_instructions(program: Any, task_prompt: str, additional_instructions: str) -> str:
    if program.policy_text.strip():
        effective = program.policy_text.rstrip() + '\n\n<official_task_instructions>\n' + task_prompt + '\n</official_task_instructions>'
    else:
        effective = task_prompt
    if not additional_instructions.strip():
        return effective
    return effective + '\n\n<harness_runtime_instructions>\n' + additional_instructions.rstrip() + '\n</harness_runtime_instructions>'

def _public_tool_result(result: CallToolResult) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    for item in result.content:
        if isinstance(item, TextContent):
            content.append({'type': 'text', 'text': item.text})
        else:
            item_type = getattr(item, 'type', None)
            content.append({'type': str(item_type or 'non_text')})
    payload: dict[str, Any] = {'is_error': bool(result.isError), 'content': content}
    structured = getattr(result, 'structuredContent', None)
    if structured is not None:
        payload['structured_content'] = _project_harness_public_value(structured)
    return payload

def _project_harness_public_arguments(arguments: dict[str, Any], *, declared_sensitive_keys: frozenset[str]) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    sensitive_count = 0
    for key, value in arguments.items():
        if key in declared_sensitive_keys:
            sensitive_count += 1
        else:
            projected[key] = _project_harness_public_value(value)
    if sensitive_count:
        for replacement in ('sensitive_argument_present', 'sensitive_argument_count'):
            if replacement in arguments or replacement in projected:
                raise ValueError('public argument projection key collision')
        projected['sensitive_argument_present'] = True
        projected['sensitive_argument_count'] = sensitive_count
    return projected

def _project_harness_public_value(value: Any) -> Any:
    if isinstance(value, dict):
        projected: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            if key == 'stored_credential_ref':
                replacement = 'sensitive_reference_present'
                if replacement in value or replacement in projected:
                    raise ValueError('public projection key collision')
                projected[replacement] = bool(item)
            else:
                projected[key] = _project_harness_public_value(item)
        return projected
    if isinstance(value, list):
        return [_project_harness_public_value(item) for item in value]
    return value

def _append_harness_guidance(result: CallToolResult, guidance: str) -> CallToolResult:
    if not guidance.strip():
        return result
    content = list(result.content)
    content.append(TextContent(type='text', text='<harness_runtime_guidance>\n' + guidance.rstrip() + '\n</harness_runtime_guidance>'))
    return result.model_copy(update={'content': content})

def _decode_internal_tool_arguments(raw_arguments: str) -> dict[str, Any]:
    try:
        value = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        raise ValueError('internal tool arguments are not valid JSON') from exc
    if not isinstance(value, dict):
        raise ValueError('internal tool arguments must be a JSON object')
    return value

def _build_open_runtime_tools(client, server):
    specifications = client.internal_tool_specifications
    use_rethinking_hooks = True

    def before_local_tool(tool_name: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        public_context: dict[str, Any] = {**server.begin_public_tool_call(), 'tool_kind': 'harness_internal'}
        try:
            decision = client.before_tool_call(tool_name, arguments, context=public_context)
        except _HARNESS_RUNTIME_ERRORS as exc:
            server.record_runtime_failure(boundary=f'before_internal_tool:{tool_name}', error=exc)
            return (public_context, HARNESS_FAIL_CLOSED_INSTRUCTION)
        if decision['decision'] != 'request_replan':
            return (public_context, None)
        message = decision['message'].strip() or 'The harness requested re-planning before this internal tool call. The harness-local tool was not executed.'
        return (public_context, '<harness_request_replan>\n' + message + '\n</harness_request_replan>')

    def after_local_tool(tool_name: str, content: str, public_context: dict[str, Any]) -> str:
        try:
            observation = client.after_tool_result(tool_name, {'is_error': False, 'content': [{'type': 'text', 'text': content}]}, context={**public_context, 'tool_result_is_error': False})
            guidance = observation['guidance']
        except _HARNESS_RUNTIME_ERRORS as exc:
            server.record_runtime_failure(boundary=f'after_internal_tool:{tool_name}', error=exc)
            guidance = HARNESS_FAIL_CLOSED_INSTRUCTION
        if not guidance.strip():
            return content
        return content.rstrip() + '\n\n<harness_runtime_guidance>\n' + guidance.rstrip() + '\n</harness_runtime_guidance>'
    tools: list[FunctionTool] = []
    for specification in specifications:
        name = specification['name']

        def build_invoke(tool_name: str) -> Callable[[Any, str], Any]:

            async def invoke(_context: Any, raw_arguments: str) -> str:
                if server.harness_fail_closed:
                    return HARNESS_FAIL_CLOSED_INSTRUCTION
                arguments = _decode_internal_tool_arguments(raw_arguments)
                public_context: dict[str, Any] | None = None
                if use_rethinking_hooks:
                    public_context, blocked_output = before_local_tool(tool_name, arguments)
                    if blocked_output is not None:
                        return blocked_output
                try:
                    result = client.invoke_internal_tool(tool_name, arguments, **{'context': public_context} if public_context is not None else {})
                except _HARNESS_RUNTIME_ERRORS as exc:
                    server.record_runtime_failure(boundary=f'internal_tool:{tool_name}', error=exc)
                    return HARNESS_FAIL_CLOSED_INSTRUCTION
                content = result['content']
                if public_context is not None:
                    return after_local_tool(tool_name, content, public_context)
                return content
            return invoke
        tools.append(FunctionTool(name=name, description=specification['description'], params_json_schema=specification['input_schema'], on_invoke_tool=build_invoke(name), strict_json_schema=True))
    return tools

async def _run_with_report_revision(*, runner, starting_agent, input, max_turns, hooks, run_config, harness_client, server, harness_failed):
    history = input
    audit = []
    for attempt in range(2):
        remaining = max_turns - hooks.model_call_starts
        if remaining <= 0:
            raise RuntimeError('No model turns remain before report revision')
        result = await runner.run(starting_agent=starting_agent, input=history, max_turns=remaining, hooks=hooks, run_config=run_config)
        draft = coerce_final_output(result.final_output)
        if draft is None or harness_client is None or harness_failed or server.harness_fallbacks:
            return (result, None, audit)
        terminal = harness_client.finalize_output(draft, context={'tool_call_count': server.public_tool_call_index, 'report_revision_attempt': attempt, 'remaining_model_turns': max_turns - hooks.model_call_starts})
        audit.append({'attempt': attempt, 'draft': draft, 'terminal': terminal['decision'], 'reason': terminal['reason'], 'model_turns_used': hooks.model_call_starts})
        if terminal['decision'] != 'revise' or attempt == 1 or hooks.model_call_starts >= max_turns:
            return (result, terminal, audit)
        history = result.to_input_list() + [{'role': 'developer', 'content': '[Public report contract check] ' + terminal['reason'] + ' You have one report revision within the original shared turn cap. Use existing observations; do not repeat completed environment actions.'}]
    raise AssertionError('Bounded report revision loop escaped')
