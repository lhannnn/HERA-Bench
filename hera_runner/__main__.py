import argparse
import asyncio
import json
from pathlib import Path
import subprocess

from .dataset import controller, download, validate
from .score import summarize


def pair_ids(value):
    if value == 'all':
        return list(range(1, 61))
    try:
        ids = [int(n.strip()) for n in value.split(',')]
    except ValueError as exc:
        raise argparse.ArgumentTypeError('Use all or comma-separated pair numbers, e.g. 1,7,46') from exc
    if not ids or len(ids) != len(set(ids)) or any(not 1 <= n <= 60 for n in ids):
        raise argparse.ArgumentTypeError('Pair numbers must be unique integers from 1 to 60')
    return ids


def main():
    parser = argparse.ArgumentParser(description='HERA benchmark and harness runner')
    commands = parser.add_subparsers(dest='command', required=True)
    get = commands.add_parser('download', help='Download and verify the pinned HF dataset')
    get.add_argument('--output', type=Path, default=Path('data/HERA-Bench'))
    check = commands.add_parser('verify', help='Replay all 120 references offline; no model calls')
    check.add_argument('--dataset', type=Path, default=Path('data/HERA-Bench'))
    run = commands.add_parser('run', help='Run a selected model; this makes billable API calls')
    run.add_argument('--dataset', type=Path, default=Path('data/HERA-Bench'))
    run.add_argument('--harness', choices=('base', 'final'), required=True)
    run.add_argument('--config', type=Path, required=True)
    run.add_argument('--pairs', type=pair_ids, default=[1], help='all or comma-separated numbers; default: 1')
    run.add_argument('--output', type=Path, required=True)
    score = commands.add_parser('score', help='Summarize an existing run without model calls')
    score.add_argument('directory', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'download':
            print(download(args.output))
        elif args.command == 'verify':
            root, _ = validate(args.dataset)
            return subprocess.run(controller(root, 'verify')).returncode
        elif args.command == 'run':
            from .run import run_batch
            config = json.loads(args.config.read_text())
            passed = asyncio.run(run_batch(args.dataset, args.output, config, args.harness, args.pairs))
            print(json.dumps(summarize(args.output), indent=2))
            return 0 if passed else 1
        else:
            print(json.dumps(summarize(args.directory), indent=2))
    except (ValueError, FileNotFoundError, FileExistsError) as exc:
        parser.exit(2, f'{exc}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
