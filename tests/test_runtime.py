import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, AsyncMock

from agents import Agent, Runner, RunConfig, set_tracing_disabled
from hera_runner.bridge import (_NameSafeMCPServer, _CompletedModelUsageHooks,
    _build_open_runtime_tools, _compose_program_instructions, _run_with_report_revision)
from hera_runner.dataset import LOCK, controller, validate
from hera_runner.harness import FinalHarness, HarnessError
from hera_runner.run import episode, run_batch
from hera_runner.score import summarize
from scripted_model import ScriptedModel

TESTS = Path(__file__).resolve().parent


class NetworkBlocked:
    def setUp(self):
        set_tracing_disabled(True)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name in ('socket.create_connection', 'socket.socket.connect', 'socket.socket.connect_ex'):
            self.stack.enter_context(patch(name, side_effect=AssertionError('Network disabled in tests')))


class Offline(NetworkBlocked, unittest.TestCase):
    def test_provider_adapters_with_mock_http(self):
        import httpx2
        from openai import AsyncOpenAI
        from agents import OpenAIChatCompletionsModel, OpenAIResponsesModel
        async def run():
            for kind, cls in [('chat', OpenAIChatCompletionsModel), ('responses', OpenAIResponsesModel)]:
                captured = []
                def respond(request):
                    captured.append(json.loads(request.content))
                    if kind == 'chat':
                        payload = {'id': 'test-chat', 'object': 'chat.completion', 'created': 0,
                            'model': 'test-model', 'choices': [{'index': 0, 'finish_reason': 'stop',
                            'message': {'role': 'assistant', 'content': 'OK'}}],
                            'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}
                    else:
                        payload = {'id': 'test-response', 'object': 'response', 'created_at': 0,
                            'model': 'test-model', 'status': 'completed',
                            'output': [{'id': 'test-message', 'type': 'message', 'role': 'assistant',
                            'status': 'completed', 'content': [{'type': 'output_text', 'text': 'OK', 'annotations': []}]}],
                            'usage': {'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2}}
                    return httpx2.Response(200, json=payload)
                transport = httpx2.MockTransport(respond)
                async with AsyncOpenAI(api_key='test-only', base_url='https://test.invalid/v1',
                        http_client=httpx2.AsyncClient(transport=transport), max_retries=0) as client:
                    model = cls(model='test-model', openai_client=client)
                    result = await Runner.run(Agent(name='Test', model=model), 'Hello',
                                              run_config=RunConfig(tracing_disabled=True))
                    self.assertEqual(result.final_output, 'OK')
                    self.assertEqual(len(captured), 1)
                    self.assertEqual(captured[0]['model'], 'test-model')
        asyncio.run(run())

    def test_base_prompt_is_byte_identical(self):
        text = 'Original prompt\n  with whitespace\n'
        self.assertEqual(_compose_program_instructions(SimpleNamespace(policy_text='\n'), text, ''), text)

    def test_hidden_context_fails_closed(self):
        harness = FinalHarness()
        with self.assertRaises(HarnessError):
            harness.before_tool_call('records.read_item', {}, context={'gold_label': 'secret'})
        self.assertTrue(harness.closed)

    def test_read_and_write_gate(self):
        h = FinalHarness()
        h.configure_runtime()
        self.assertEqual(h.before_tool_call('records.read_item', {})['decision'], 'allow')
        self.assertEqual(h.before_tool_call('records.set_value', {'value': 4})['decision'], 'request_replan')

    def test_internal_tool_limit(self):
        h = FinalHarness(); h.configure_runtime(); h.internal_tool_calls = 128
        with self.assertRaises(HarnessError):
            h.invoke_internal_tool('harness_review_action', {})

    def test_sdk_mcp_read_and_internal_tool(self):
        async def run():
            h = FinalHarness(); h.configure_runtime()
            async with _NameSafeMCPServer(params={'command': sys.executable,
                    'args': ['-I', '-B', str(TESTS / 'fixture_server.py')]},
                    harness_program=h, client_session_timeout_seconds=30) as server:
                await server.list_tools()
                model = ScriptedModel([
                    ('records__read_item', {}),
                    ('harness_review_action', {'next_tool': 'records__read_item',
                       'next_arguments_json': '{}', 'verdict': 'inspect', 'conditions': [],
                       'explanation': 'Read-only synthetic record lookup.'}),
                    'Done'])
                agent = Agent(name='Test', model=model, instructions='Read the record.',
                              mcp_servers=[server], tools=_build_open_runtime_tools(h, server))
                result = await Runner.run(agent, 'Read it', max_turns=5,
                                          run_config=RunConfig(tracing_disabled=True))
                self.assertEqual(result.final_output, 'Done')
                self.assertFalse(server.harness_fallbacks)
                self.assertEqual(h.internal_tool_calls, 1)
                self.assertTrue(h.state['observations'])
                observed = json.dumps(model.requests[-1]['input'])
                self.assertIn('record-a', observed)
                self.assertIn('harness_runtime_guidance', observed)
        asyncio.run(run())

    def test_revision_reuses_history_and_shared_turn_budget(self):
        class Gate:
            def finalize_output(self, draft, *, context):
                return {'decision': 'revise' if draft == 'bad' else 'accept', 'reason': 'fix report'}
        async def run():
            model = ScriptedModel(['bad', 'good'])
            hooks = _CompletedModelUsageHooks()
            result, terminal, audit = await _run_with_report_revision(
                runner=Runner, starting_agent=Agent(name='Test', model=model), input='task', max_turns=2,
                hooks=hooks, run_config=RunConfig(tracing_disabled=True), harness_client=Gate(),
                server=SimpleNamespace(harness_fallbacks=[], public_tool_call_index=0), harness_failed=False)
            self.assertEqual(result.final_output, 'good')
            self.assertEqual(hooks.model_call_starts, 2)
            self.assertEqual(len(audit), 2)
            self.assertIn('bad', json.dumps(model.requests[1]['input']))
            limited = ScriptedModel(['bad'])
            _, terminal, audit = await _run_with_report_revision(
                runner=Runner, starting_agent=Agent(name='Test', model=limited), input='task', max_turns=1,
                hooks=_CompletedModelUsageHooks(), run_config=RunConfig(tracing_disabled=True),
                harness_client=Gate(), server=SimpleNamespace(harness_fallbacks=[], public_tool_call_index=0),
                harness_failed=False)
            self.assertEqual(len(limited.requests), 1)
            self.assertEqual(terminal['decision'], 'revise')
        asyncio.run(run())

    def test_paired_metrics_missing_failure_and_wrong_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'run.json').write_text(json.dumps({'pairs': [1, 2], 'harness': 'base',
                          'dataset': LOCK, 'code_sha256': 'test'}))
            def put(n, side, passed, valid=True):
                p = root/f'hera_{n:03}'/side/'result.json'; p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps({'number': n, 'side': side, 'pair_id': f'hera_{n:03}',
                                        'harness': 'base', 'passed': passed, 'runtime_valid': valid}))
                return p
            put(1, 'act', True); put(1, 'abstain', True); put(2, 'act', False, False)
            self.assertIsNone(summarize(root)['percentages'])
            p = put(2, 'abstain', True)
            report = summarize(root)
            self.assertEqual(report['percentages'], {'Act': 50, 'Abstain': 100, 'Pair': 50})
            self.assertEqual(report['runtime_failures'], 1)
            self.assertEqual(report['scope'], 'subset')
            d=json.loads(p.read_text()); d['number']=1; p.write_text(json.dumps(d))
            with self.assertRaises(ValueError): summarize(root)


@unittest.skipUnless(os.environ.get('HERA_DATASET'), 'Set HERA_DATASET for executable dataset integration')
class DatasetIntegration(NetworkBlocked, unittest.TestCase):
    def test_batch_to_summary(self):
        root, tasks = validate(os.environ['HERA_DATASET'])
        env = json.loads((root/'index/test.jsonl').read_text().splitlines()[0])['environment']
        steps = []
        for side in ('act', 'abstain'):
            raw = subprocess.run(controller(root, 'reference', '--number', 1, '--side', side),
                                 capture_output=True, text=True, check=True)
            reference = json.loads(raw.stdout)
            steps += [(_NameSafeMCPServer._encode(env+'.'+e['tool']), e['arguments']) for e in reference['events']]
            steps += [json.dumps(reference['answer'])]
        model = ScriptedModel(steps)
        client = SimpleNamespace(close=AsyncMock())
        with tempfile.TemporaryDirectory() as tmp, patch('hera_runner.run.configured_model', return_value=(model, client)):
            out = Path(tmp)/'run'
            success = asyncio.run(run_batch(root, out, {'model': 'scripted-test'}, 'base', [1]))
            self.assertTrue(success)
            self.assertEqual(summarize(out)['percentages'], {'Act': 100, 'Abstain': 100, 'Pair': 100})
            self.assertFalse(model.steps)
            client.close.assert_awaited_once()
            with self.assertRaises(FileExistsError):
                asyncio.run(run_batch(root, out, {'model': 'scripted-test'}, 'base', [1]))

    def test_real_environments_base_and_final(self):
        root, tasks = validate(os.environ['HERA_DATASET'])
        rows = [json.loads(s) for s in (root/'index/test.jsonl').read_text().splitlines()]
        cases = [(1, 'act', 'base'), (1, 'abstain', 'base'), (46, 'act', 'base'),
                 (1, 'act', 'final'), (1, 'abstain', 'final'), (7, 'abstain', 'final')]
        with tempfile.TemporaryDirectory() as tmp:
            for number, side, harness in cases:
                with self.subTest(number=number, side=side, harness=harness):
                    raw = subprocess.run(controller(root, 'reference', '--number', number, '--side', side),
                                         capture_output=True, text=True, check=True)
                    reference = json.loads(raw.stdout)
                    steps = [('task_documents__list_documents', {}),
                             ('task_documents__read_document', {'document_id': f'brief-{number:03}'})]
                    steps += [(_NameSafeMCPServer._encode(rows[number-1]['environment']+'.'+e['tool']), e['arguments'])
                              for e in reference['events']]
                    answer = json.dumps(reference['answer'])
                    if harness == 'final' and number == 1:
                        steps += ['{}', answer]  # Exercise the real public report repair gate.
                    else:
                        steps += [answer]
                    model = ScriptedModel(steps)
                    out = Path(tmp)/f'{harness}-{number}-{side}'
                    result = asyncio.run(episode(root=root, task=tasks[number-1], side=side,
                        harness=harness, model=model, settings={}, out=out))
                    self.assertTrue(result['runtime_valid'], result)
                    self.assertTrue(result['passed'], result)
                    self.assertEqual(json.loads((out/'record.json').read_text())['final_state'], reference['final_state'])
                    self.assertFalse(model.steps)
                    if harness == 'base':
                        self.assertEqual(model.requests[0]['system_instructions'], tasks[number-1]['system_prompt'])
                    self.assertEqual(model.requests[0]['input'][0]['content'], tasks[number-1]['instruction'])

    def test_model_failure_is_recorded_without_retry(self):
        root, tasks = validate(os.environ['HERA_DATASET'])
        with tempfile.TemporaryDirectory() as tmp:
            model = ScriptedModel([RuntimeError('sensitive provider response')])
            out = Path(tmp)/'failed'
            result = asyncio.run(episode(root=root, task=tasks[0], side='act', harness='base',
                                        model=model, settings={}, out=out))
            self.assertFalse(result['runtime_valid'])
            self.assertFalse(result['passed'])
            self.assertEqual(len(model.requests), 1)
            self.assertEqual(result['unresolved_model_calls'], 1)
            self.assertNotIn('sensitive provider response', (out/'result.json').read_text())
