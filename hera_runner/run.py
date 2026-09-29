"""Run one fixed harness over paired MCP episodes and record trusted scores."""
from __future__ import annotations
import asyncio
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
from types import SimpleNamespace

from agents import Agent, ModelSettings, Runner, RunConfig, set_tracing_disabled
from agents import OpenAIChatCompletionsModel, OpenAIResponsesModel
from openai import AsyncOpenAI

from .bridge import (_NameSafeMCPServer, _CompletedModelUsageHooks,
                     _build_open_runtime_tools, _compose_program_instructions,
                     _run_with_report_revision, coerce_final_output)
from .harness import FinalHarness, ROOT, verify_components
from .dataset import LOCK, controller, grade, validate


def write(path, value):
    with Path(path).open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')


def code_identity():
    paths = sorted(p for d in ('hera_runner', 'harnesses') for p in (ROOT / d).rglob('*')
                   if p.is_file() and '__pycache__' not in p.parts)
    paths += [ROOT / 'requirements.txt', ROOT / 'dataset.lock.json']
    entries = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()


async def episode(*, root, task, side, harness, model, settings, out, max_turns=30):
    """`model` is injectable for offline SDK tests; it never receives controller metadata."""
    set_tracing_disabled(True)
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    record = out / 'record.json'
    hooks = _CompletedModelUsageHooks()
    client = None
    response = None
    terminal = None
    revision_audit = []
    error = None
    fallbacks = []
    phase = 'harness_setup'
    try:
        if harness == 'final':
            client = FinalHarness()
            client.configure_runtime(context={'phase': 'rollout_setup'})
            addition = client.compose_instructions(task['system_prompt'],
                         context={'user_instruction': task['instruction']})['additional_instructions']
            prompt = _compose_program_instructions(client, task['system_prompt'], addition)
        elif harness == 'base':
            verify_components()
            policy = (ROOT / 'harnesses/base/policy.md').read_text()
            prompt = _compose_program_instructions(SimpleNamespace(policy_text=policy), task['system_prompt'], '')
        else:
            raise ValueError('Unknown harness')
        command = controller(root, 'serve', '--number', task['number'], '--side', side, '--record', record)
        env = {k: os.environ[k] for k in ('PATH', 'SYSTEMROOT', 'LANG') if k in os.environ}
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        phase = 'environment_startup'
        async with _NameSafeMCPServer(name='HERA tools',
                params={'command': command[0], 'args': command[1:], 'cwd': str(out), 'env': env},
                client_session_timeout_seconds=180, cache_tools_list=True, harness_program=client,
                max_retry_attempts=0) as server:
            tools = await server.list_tools()
            internal = _build_open_runtime_tools(client, server) if client else []
            if {t.name for t in tools} & {t.name for t in internal}:
                raise ValueError('Environment and harness tool names collide')
            agent = Agent(name='HERA agent', instructions=prompt, model=model,
                          model_settings=ModelSettings(**settings, retry={'max_retries': 0}),
                          mcp_servers=[server], tools=internal)
            phase = 'agent_run'
            result, terminal, revision_audit = await _run_with_report_revision(
                runner=Runner, starting_agent=agent, input=task['instruction'], max_turns=max_turns,
                hooks=hooks, run_config=RunConfig(tracing_disabled=True),
                harness_client=client, server=server, harness_failed=False)
            response = coerce_final_output(result.final_output)
            fallbacks = server.harness_fallbacks
            if response is None or fallbacks:
                raise RuntimeError('Harness or model produced no valid final response')
            if terminal and terminal['decision'] in ('replace', 'abstain'):
                response = terminal['output']
    except Exception as exc:
        # Provider exception strings may contain request headers or secrets.
        error = {'phase': phase, 'type': type(exc).__name__}
    finally:
        if client:
            client.close()

    verdict = None
    if error is None:
        try:
            submission = json.loads(record.read_text())
            submission['final_output'] = response
            write(out / 'submission.json', submission)
            verdict = grade(root, task['number'], side, out / 'submission.json', out / 'grade.json')
        except Exception as exc:
            error = {'phase': 'grading', 'type': type(exc).__name__}
    status = {
        'number': task['number'], 'pair_id': f"hera_{task['number']:03}", 'side': side,
        'harness': harness, 'runtime_valid': error is None, 'error': error,
        'passed': bool(verdict and verdict.get('passed') and error is None),
        'usage': hooks.metadata(), 'model_calls_started': hooks.model_call_starts,
        'unresolved_model_calls': max(0, hooks.model_call_starts - ((hooks.metadata() or {}).get('num_responses', 0))),
        'terminal_decision': terminal['decision'] if terminal else None,
        'report_revisions': [{k: v for k, v in item.items() if k != 'draft'} for item in revision_audit],
        'harness_fallbacks': fallbacks,
    }
    write(out / 'result.json', status)
    return status


def configured_model(config):
    if not isinstance(config.get('model'), str) or not config['model'].strip() or config['model'] == 'YOUR_MODEL_ID':
        raise ValueError('Set model to the exact provider model ID in your configuration.')
    key_name = config.get('api_key_env', 'OPENAI_API_KEY')
    if not os.environ.get(key_name):
        raise ValueError(f'Set the {key_name} environment variable locally.')
    base_url = os.environ.get(config.get('base_url_env', 'OPENAI_BASE_URL'))
    sdk_client = AsyncOpenAI(api_key=os.environ[key_name], base_url=base_url,
                            max_retries=0, timeout=config.get('timeout_seconds', 180))
    cls = OpenAIResponsesModel if config.get('api', 'responses') == 'responses' else OpenAIChatCompletionsModel
    return cls(model=config['model'], openai_client=sdk_client), sdk_client


async def run_batch(root, out, config, harness, pairs):
    root, tasks = validate(root)
    verify_components()
    out = Path(out).resolve()
    if out.is_relative_to(root):
        raise ValueError('Store run output outside the dataset directory.')
    if out.exists():
        raise FileExistsError('Use a new output directory; runs are never retried or overwritten.')
    max_turns = config.get('max_turns', 30)
    if type(max_turns) is not int or not 1 <= max_turns <= 30:
        raise ValueError('max_turns must be between 1 and 30.')
    if config.get('api', 'responses') not in ('responses', 'chat_completions'):
        raise ValueError('api must be responses or chat_completions.')
    allowed = {'model', 'api', 'api_key_env', 'base_url_env', 'max_turns', 'max_tokens',
               'temperature', 'reasoning_effort', 'timeout_seconds'}
    if set(config) - allowed:
        raise ValueError('Unknown configuration fields; credentials belong in environment variables.')
    settings = {k: config[k] for k in ('max_tokens', 'temperature') if config.get(k) is not None}
    if config.get('reasoning_effort'):
        settings['reasoning'] = {'effort': config['reasoning_effort']}
    ModelSettings(**settings)
    model, sdk_client = configured_model(config)
    out.mkdir(parents=True)
    metadata = {'dataset': LOCK, 'harness': harness, 'pairs': pairs, 'settings': config,
                'code_sha256': code_identity(), 'versions': {n: importlib.metadata.version(n)
                    for n in ('openai-agents', 'openai', 'mcp', 'fastmcp')},
                'retry_policy': 'none; stop batch on runtime error', 'tracing': False}
    write(out / 'run.json', metadata)
    try:
        for number in pairs:
            for side in ('act', 'abstain'):
                result = await episode(root=root, task=tasks[number-1], side=side, harness=harness,
                    model=model, settings=settings, out=out / f'hera_{number:03}' / side, max_turns=max_turns)
                print(json.dumps({k: result[k] for k in ('pair_id', 'side', 'runtime_valid', 'passed')}), flush=True)
                if not result['runtime_valid']:
                    return False
        return True
    finally:
        await sdk_client.close()
