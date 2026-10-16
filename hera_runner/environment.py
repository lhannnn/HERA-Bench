from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from agents.mcp.server import MCPServer, MCPServerStdio
from mcp.types import CallToolResult, TextContent, Tool

from .bridge import (
    HARNESS_FAIL_CLOSED_INSTRUCTION,
    _CREDENTIAL_LIKE_TOOL_ARGUMENT_KEYS,
    _append_harness_guidance,
    _project_harness_public_arguments,
    _public_tool_result,
)
from .harness import HarnessError


def public_tool_name(name: str) -> str:
    parts = name.split('.')
    public = '__'.join(parts)
    if len(public) > 64:
        digest = sha256(name.encode('utf-8')).hexdigest()[:12]
        public = f'{public[:49]}__h{digest}'
    return public


@dataclass(frozen=True)
class ToolRoute:
    original: Tool
    exposed: Tool
    protected_arguments: frozenset[str]

    @classmethod
    def from_tool(cls, tool: Tool) -> ToolRoute:
        schema = tool.inputSchema
        properties = schema.get('properties', {}) if isinstance(schema, dict) else {}
        if not isinstance(properties, dict):
            raise RuntimeError(f'Invalid argument properties for {tool.name!r}')
        exposed = tool.model_copy(update={'name': public_tool_name(tool.name)})
        protected = frozenset(properties).intersection(_CREDENTIAL_LIKE_TOOL_ARGUMENT_KEYS)
        return cls(tool, exposed, protected)

    def contract(self) -> dict[str, Any]:
        tool = self.original
        return dict(
            name=tool.name,
            description=tool.description,
            inputSchema=tool.inputSchema,
            annotations=tool.annotations.model_dump(mode='json') if tool.annotations else None,
        )


class EnvironmentServer(MCPServer):

    def __init__(self, *, harness_program=None, fail_closed=False, **transport_options):
        policy_options = {
            name: transport_options[name]
            for name in (
                'use_structured_content', 'require_approval', 'failure_error_function',
                'tool_meta_resolver', 'custom_data_extractor',
                'tool_input_guardrails', 'tool_output_guardrails',
            )
            if name in transport_options
        }
        super().__init__(**policy_options)
        self.transport = MCPServerStdio(**transport_options)
        self.harness = harness_program
        self._halted = bool(fail_closed)
        self._failures = []
        self._calls = 0
        self._routes: dict[str, ToolRoute] = {}
        self._by_original: dict[str, ToolRoute] = {}
        self._catalog: list[Tool] | None = None

    @property
    def name(self):
        return self.transport.name

    async def connect(self):
        await self.transport.connect()

    async def cleanup(self):
        await self.transport.cleanup()

    async def __aenter__(self):
        await self.transport.__aenter__()
        return self

    async def __aexit__(self, *exc):
        return await self.transport.__aexit__(*exc)

    async def list_prompts(self):
        return await self.transport.list_prompts()

    async def get_prompt(self, name, arguments=None):
        return await self.transport.get_prompt(name, arguments)

    @property
    def cached_tools(self):
        if self._halted:
            return []
        return None if self._catalog is None else list(self._catalog)

    async def list_tools(self, run_context=None, agent=None):
        if self._halted:
            return []
        source = await self.transport.list_tools(run_context, agent)
        routes = [ToolRoute.from_tool(tool) for tool in source]
        aliases = {}
        for route in routes:
            name = route.exposed.name
            if name in aliases and aliases[name].original.name != route.original.name:
                raise RuntimeError(f'Ambiguous public tool name: {name!r}')
            aliases[name] = route
        self._routes = aliases
        self._by_original = {route.original.name: route for route in routes}
        self._catalog = [route.exposed for route in routes]
        return list(self._catalog)

    @property
    def harness_fallbacks(self):
        return [dict(item) for item in self._failures]

    @property
    def harness_fail_closed(self):
        return self._halted

    @property
    def public_tool_call_index(self):
        return self._calls

    def begin_public_tool_call(self):
        self._calls += 1
        return {'tool_call_index': self._calls}

    def record_runtime_failure(self, *, boundary, error):
        self._halted = True
        self._failures.append(dict(
            boundary=boundary,
            error_kind=str(getattr(error, 'kind', type(error).__name__)),
        ))
        harness, self.harness = self.harness, None
        if harness is not None:
            harness.close()

    @staticmethod
    def blocked_result(decision, message):
        tag = 'harness_fail_closed' if decision == 'fail_closed' else 'harness_request_replan'
        return CallToolResult(
            isError=True,
            content=[TextContent(type='text', text=f'<{tag}>\n{message}\n</{tag}>')],
            structuredContent={'harness_decision': decision, 'tool_executed': False},
        )

    async def call_tool(self, tool_name, arguments, meta=None):
        if self._halted:
            return self.blocked_result('fail_closed', HARNESS_FAIL_CLOSED_INSTRUCTION)
        route = self._routes.get(tool_name) or self._by_original.get(tool_name)
        name = route.original.name if route is not None else tool_name
        harness = self.harness
        if harness is None:
            return await self.transport.call_tool(name, arguments, meta=meta)

        context = self.begin_public_tool_call()
        context['tool_contract'] = route.contract() if route is not None else None
        public_arguments = _project_harness_public_arguments(
            dict(arguments or {}),
            declared_sensitive_keys=route.protected_arguments if route else frozenset(),
        )
        try:
            decision = harness.before_tool_call(name, public_arguments, context=context)
        except HarnessError as error:
            self.record_runtime_failure(boundary='before_tool_call', error=error)
            return self.blocked_result('fail_closed', HARNESS_FAIL_CLOSED_INSTRUCTION)
        if decision['decision'] == 'request_replan':
            reason = decision['message'].strip()
            if not reason:
                reason = 'The harness requested re-planning before this tool call. The environment tool was not executed.'
            return self.blocked_result('request_replan', reason)

        result = await self.transport.call_tool(name, arguments, meta=meta)
        observation_context = dict(context, tool_result_is_error=bool(result.isError))
        try:
            feedback = harness.after_tool_result(name, _public_tool_result(result), context=observation_context)
            guidance = feedback['guidance']
        except HarnessError as error:
            self.record_runtime_failure(boundary='after_tool_result', error=error)
            guidance = HARNESS_FAIL_CLOSED_INSTRUCTION
        return _append_harness_guidance(result, guidance)
