"""Deterministic SDK test double. Never contacts a model provider."""
import json
from agents import Model, ModelResponse, Usage
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText


class ScriptedModel(Model):
    def __init__(self, steps):
        self.steps = list(steps)
        self.requests = []

    async def get_response(self, *args, **kwargs):
        self.requests.append(kwargs)
        if not self.steps:
            raise AssertionError('Unexpected model call')
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        call = str(len(self.requests))
        if isinstance(step, tuple):
            name, arguments = step
            item = ResponseFunctionToolCall(name=name, arguments=json.dumps(arguments),
                    call_id='call_'+call, id='fc_'+call, type='function_call')
        else:
            item = ResponseOutputMessage(id='msg_'+call, role='assistant', status='completed',
                    type='message', content=[ResponseOutputText(type='output_text', text=step, annotations=[])])
        return ModelResponse(output=[item], usage=Usage(requests=1, input_tokens=1,
                             output_tokens=1, total_tokens=2), response_id='response_'+call)

    async def stream_response(self, *args, **kwargs):
        raise AssertionError('Streaming is not used by these tests')
        yield
