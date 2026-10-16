import asyncio
from copy import deepcopy
import os
from pathlib import Path
import sys
import unittest

from fastmcp import Client
from fastmcp.exceptions import ValidationError as MCPValidationError


DATASET = Path(os.environ.get('HERA_DATASET', '.'))
AVAILABLE = (DATASET / 'runtime/hera_environment/base.py').is_file()
if AVAILABLE:
    sys.path.insert(0, str(DATASET / 'runtime'))
    from hera_environment import BaseEnvironment, ToolError

    class Counter(BaseEnvironment):
        env_name = 'counter'
        tool_kinds = {'read': 'lookup', 'increment': 'commit'}

        def _load_state(self, initial_state):
            return deepcopy(initial_state)

        def _register_tools(self):
            self.mcp.tool(self.read)
            self.mcp.tool(self.increment)

        def read(self) -> int:
            return self.state['value']

        def increment(self, amount: int) -> int:
            self.state['value'] += amount
            self._log_tool_call('increment', {'amount': amount}, self.state['value'])
            return self.state['value']

        @classmethod
        def get_empty_state(cls):
            return {'value': 0}

        @classmethod
        def get_example_state(cls):
            return {'value': 5}

        @classmethod
        def get_state_schema(cls):
            return {'type': 'object', 'properties': {'value': {'type': 'integer'}}}


@unittest.skipUnless(AVAILABLE, 'Set HERA_DATASET to the HERA runtime dataset')
class EnvironmentState(unittest.TestCase):
    def test_sync_calls_state_and_log(self):
        initial = {'value': 1}
        env = Counter(initial)
        self.assertEqual(env.call_tool('increment', amount=2), 3)
        self.assertEqual(initial, {'value': 1})
        self.assertEqual(env._initial_state_snapshot, {'value': 1})
        self.assertEqual(env.get_execution_log(), [dict(
            tool='increment', params={'amount': 2}, result=3, success=True, error=None)])
        self.assertEqual(env.get_tool_callable('read')(), 3)

    def test_sync_facade_inside_running_loop(self):
        async def run():
            env = Counter({'value': 1})
            self.assertEqual(env.call_tool('increment', amount=3), 4)
            self.assertEqual(env.get_tool_callable('read')(), 4)
            schemas = Counter.get_tool_schemas()
            self.assertEqual({s['name']:s['kind'] for s in schemas},
                             {'read': 'lookup', 'increment': 'commit'})
        asyncio.run(run())

    def test_broken_and_hidden_tools(self):
        env = Counter({'value': 7})
        env.break_tool('increment', 'Disabled for fixture')
        with self.assertRaisesRegex(ToolError, 'Disabled'):
            env.call_tool('increment', amount=2)
        self.assertEqual(env.state['value'], 7)
        self.assertFalse(env.get_execution_log()[-1]['success'])

        async def run():
            async with Client(env.mcp) as client:
                result = await client.call_tool('increment', {'amount': 2}, raise_on_error=False)
                self.assertTrue(result.is_error)
                env.hide_tool('read')
                self.assertNotIn('read', [tool.name for tool in await client.list_tools()])
            self.assertEqual(env.state['value'], 7)
        asyncio.run(run())

    def test_invalid_arguments_preserve_sdk_error_and_state(self):
        env = Counter({'value': 7})
        with self.assertRaises(MCPValidationError):
            env.call_tool('increment', amount='not-an-integer')
        self.assertEqual(env.state['value'], 7)
