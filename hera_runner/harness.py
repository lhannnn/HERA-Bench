"""Adapter for the two fixed, hash-verified HERA harness components."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from . import contracts

ROOT = Path(__file__).resolve().parents[1]


class HarnessError(RuntimeError):
    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


def verify_components():
    manifest = json.loads((ROOT / 'harnesses/components.json').read_text())
    for rel, expected in manifest['files'].items():
        path = ROOT / rel
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise HarnessError('component_integrity_mismatch')
    return manifest


class FinalHarness:
    """Preserve the evaluated seven-hook ABI for the fixed final component.

    This portable adapter is not a sandbox for arbitrary candidate code.
    """
    def __init__(self):
        verify_components()
        root = ROOT / 'harnesses/final'
        self.config = json.loads((root / 'code_agent.json').read_text())
        self.policy_text = (root / 'systemprompt.md').read_text()
        spec = importlib.util.spec_from_file_location('hera_final', root / self.config['entrypoint'])
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.state = {}
        self.closed = False
        self.configured = False
        self.internal_tool_calls = 0
        self._call('initialize')

    def _call(self, hook, *args, context=None, base_instructions=None):
        if self.closed:
            raise HarnessError('harness_closed')
        try:
            public = dict(context or {})
            contracts._assert_public_json({'context': public, 'arguments_for_hook': list(args)}, label=hook)
            ctx = contracts._component_context(public, {'id': 'entrypoint', 'params': {}}, self.state)
            if base_instructions is not None:
                contracts._assert_public_json(base_instructions, label='base_instructions')
                ctx['base_instructions'] = base_instructions
            raw = getattr(self.module, hook)(ctx, *contracts._json_clone(list(args)))
            result, state = contracts._validate_hook_result(hook, raw, max_state_bytes=256 * 1024)
            # The evaluated graph also bounds the aggregate middleware state.
            contracts._validate_state({'entrypoint': state}, 256 * 1024)
            self.state = state
            return result
        except Exception as exc:
            self.closed = True
            raise HarnessError('invalid_' + hook) from exc

    def configure_runtime(self, *, context=None):
        if self.configured:
            raise HarnessError('already_configured')
        result = self._call('configure_runtime', context=context)
        expected = [{'name': t['name'], 'description': t['description']} for t in self.config['tools']]
        if result['internal_tools'] != expected or result['subagents']:
            raise HarnessError('capability_declaration_mismatch')
        self.configured = True
        return result

    @property
    def internal_tool_specifications(self):
        return contracts._json_clone(self.config['tools'])

    def compose_instructions(self, base_instructions, *, context=None):
        return self._call('compose_instructions', context=context, base_instructions=base_instructions)

    def before_tool_call(self, name, arguments, *, context=None):
        return self._call('before_tool_call', name, arguments, context=context)

    def after_tool_result(self, name, result, *, context=None):
        return self._call('after_tool_result', name, result, context=context)

    def invoke_internal_tool(self, name, arguments, *, context=None):
        if not self.configured or name not in {t['name'] for t in self.config['tools']}:
            raise HarnessError('undeclared_internal_tool')
        if self.internal_tool_calls >= 128:
            raise HarnessError('internal_tool_budget_exceeded')
        self.internal_tool_calls += 1
        return self._call('invoke_internal_tool', name, arguments, context=context)

    def finalize_output(self, draft, *, context=None):
        return self._call('finalize_output', draft, context=context)

    def close(self):
        self.closed = True
