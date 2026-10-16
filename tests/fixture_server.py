"""Synthetic tool server for SDK integration tests; no benchmark answers."""
from fastmcp import FastMCP
server = FastMCP('Synthetic test tools')


@server.tool(name='records.read_item', annotations={'readOnlyHint': True})
def read_item():
    return {'id': 'record-a', 'value': 2}


@server.tool(name='records.set_value', annotations={'readOnlyHint': False})
def set_value(value: int):
    return {'id': 'record-a', 'value': value}


if __name__ == '__main__':
    server.run(transport='stdio', show_banner=False)
