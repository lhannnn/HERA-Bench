import asyncio
from hashlib import sha256
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from mcp.types import CallToolResult, TextContent, Tool

from hera_runner.environment import EnvironmentServer, public_tool_name
from hera_runner.harness import HarnessError


class EnvironmentRouting(unittest.IsolatedAsyncioTestCase):
    def server(self, tools, harness=None):
        server = EnvironmentServer(params={'command': 'unused'}, harness_program=harness)
        server.transport = SimpleNamespace(
            name='Fixture', list_tools=AsyncMock(return_value=tools),
            call_tool=AsyncMock(return_value=CallToolResult(
                content=[TextContent(type='text', text='observation')],
                structuredContent={'record': 'r1'}, isError=False,
            )),
        )
        return server

    async def test_catalog_and_transport_preserve_schema_arguments_and_meta(self):
        tool = Tool(name='records.read', description='Read a record', inputSchema={
            'type': 'object', 'properties': {'record': {'type': 'string'}},
        })
        server = self.server([tool])
        exposed = await server.list_tools()
        self.assertEqual(exposed[0].name, 'records__read')
        self.assertEqual(exposed[0].inputSchema, tool.inputSchema)
        self.assertEqual(tool.name, 'records.read')
        arguments, meta = {'record': 'r1'}, {'request': 'r2'}
        result = await server.call_tool('records__read', arguments, meta)
        server.transport.call_tool.assert_awaited_once_with('records.read', arguments, meta=meta)
        self.assertEqual(result.structuredContent, {'record': 'r1'})
        self.assertEqual(server.public_tool_call_index, 0)

    async def test_alias_collision_does_not_replace_previous_catalog(self):
        tool = lambda name: Tool(name=name, inputSchema={'type': 'object'})
        server = self.server([tool('record.read')])
        await server.list_tools()
        server.transport.list_tools.return_value = [tool('record.read'), tool('record__read')]
        with self.assertRaisesRegex(RuntimeError, 'Ambiguous'):
            await server.list_tools()
        self.assertEqual([t.name for t in server.cached_tools], ['record__read'])

    async def test_hook_projection_keeps_original_wire_arguments(self):
        harness = Mock()
        harness.before_tool_call.return_value = {'decision': 'allow'}
        harness.after_tool_result.return_value = {'guidance': 'Check the record.'}
        tool = Tool(name='records.read', inputSchema={
            'type': 'object', 'properties': {'api_key': {'type': 'string'}, 'record': {'type': 'string'}},
        })
        server = self.server([tool], harness)
        await server.list_tools()
        arguments = {'api_key': 'synthetic-opaque-value', 'record': 'r1'}
        result = await server.call_tool('records__read', arguments)
        server.transport.call_tool.assert_awaited_once_with('records.read', arguments, meta=None)
        projected = harness.before_tool_call.call_args.args[1]
        self.assertNotIn('api_key', projected)
        self.assertEqual(projected['sensitive_argument_count'], 1)
        context = harness.before_tool_call.call_args.kwargs['context']
        self.assertEqual(context['tool_contract']['name'], 'records.read')
        self.assertEqual(context['tool_call_index'], 1)
        self.assertIn('Check the record.', result.content[-1].text)

    async def test_replan_prevents_execution(self):
        harness = Mock()
        harness.before_tool_call.return_value = {'decision': 'request_replan', 'message': 'Need evidence.'}
        server = self.server([Tool(name='records.write', inputSchema={})], harness)
        await server.list_tools()
        result = await server.call_tool('records__write', {})
        self.assertTrue(result.isError)
        self.assertFalse(result.structuredContent['tool_executed'])
        server.transport.call_tool.assert_not_awaited()
        harness.after_tool_result.assert_not_called()

    async def test_hook_failure_stops_later_calls(self):
        harness = Mock()
        harness.before_tool_call.side_effect = HarnessError('fixture failure')
        server = self.server([Tool(name='records.write', inputSchema={})], harness)
        await server.list_tools()
        for _ in range(2):
            result = await server.call_tool('records__write', {})
            self.assertEqual(result.structuredContent['harness_decision'], 'fail_closed')
        server.transport.call_tool.assert_not_awaited()
        harness.close.assert_called_once()
        self.assertEqual(await server.list_tools(), [])
        self.assertEqual(server.harness_fallbacks[0]['boundary'], 'before_tool_call')

    async def test_long_names_keep_evaluated_aliases(self):
        name = 'records.' + 'x' * 70
        expected = ('records__' + 'x' * 70)[:49] + '__h' + sha256(name.encode()).hexdigest()[:12]
        self.assertEqual(public_tool_name(name), expected)
        self.assertEqual(len(expected), 64)
