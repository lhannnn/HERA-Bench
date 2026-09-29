# Running HERA-Bench

## Data and configuration

Use Python 3.12. `python -m hera_runner download` retrieves the commit in `dataset.lock.json` and checks every file against its pinned checksum manifest. Authenticate with `hf auth login` while the dataset is private. The dataset remains on Hugging Face; this code repository does not duplicate its tasks or states.

Copy `configs/model.example.json` to your own configuration and set the exact provider model ID. `api` is `responses` or `chat_completions`; credentials are read from the named environment variable. An optional `OPENAI_BASE_URL` selects an OpenAI-compatible endpoint. Temperature and reasoning effort are omitted when null. The example sets a 30-model-turn cap shared with any final-report revision and an 8,192-token per-response cap; use and report settings supported by the selected model. A turn cap is not a dollar budget.

## Run and score

```bash
python -m hera_runner run --harness base --config configs/model.json \
  --pairs all --output runs/base
python -m hera_runner run --harness final --config configs/model.json \
  --pairs all --output runs/final
python -m hera_runner score runs/base
python -m hera_runner score runs/final
```

Each selected pair runs `act` then `abstain` with a fresh environment, model conversation, and harness state. `--pairs 1,7,46` selects a subset. The default is pair 1, both sides. Every run requires a new output directory and records the dataset revision, code digest, model settings, dependency versions, trusted tool events, final state, response, and grading result. API and MCP retries are disabled. A runtime error stops the batch; completed usage and unresolved model calls remain recorded. Review partial output before starting another run.

Act is correct feasible-task completion; Abstain is the correct response to the paired infeasible task; Pair requires both. Percentages use the selected pair count, and subset runs are explicitly labeled. A complete 60-pair run uses a denominator of 60. Runtime failures count as unsuccessful; missing episodes make the run incomplete and suppress accuracy percentages. Over-abstention is not computed as `100 - Act`.

## Evaluation boundary

The solver receives only the shared task prompts, public MCP tools, their observations, and the selected harness guidance. Side labels, references, states, and grading stay in the controller. No shell or filesystem tool is given to the solver. Tracing to the SDK service is disabled; model requests still go to the explicitly configured provider. Run artifacts contain benchmark evidence and should remain local until intended for release.

The base policy and final `R10::source_report_v1` components are byte-identical to the evaluated source files, bound by `harnesses/components.json`. The final harness includes human-assisted repairs. The shared bridge preserves prompt composition, tool-name mapping, public hook inputs, action gating, and one bounded report-revision attempt within the original turn cap. The public hook validators retain the evaluated runtime's checks.

This is a portable runner for these fixed components. It replaces the research campaign's Docker-based candidate controller with a local adapter; it is not a sandbox for arbitrary generated code or a reproduction of the harness-evolution pipeline. Offline checks validate execution and integration, not the paper's model scores. Reproducing those scores requires the same model/provider versions and inference settings, in addition to these data and harness versions.

## Offline checks

```bash
python -m unittest discover -s tests -v
python -m hera_runner verify
HERA_DATASET=data/HERA-Bench python -m unittest discover -s tests -v
```

The first command checks hook contracts, SDK orchestration with a scripted model, and aggregation. The second replays all 120 benchmark references. Setting `HERA_DATASET` also enables integration checks against real dataset environments with a scripted model. These checks make no model API calls; scripted outputs are test inputs, not model-performance results.

## Licensing

The extracted runtime components retain the [AgentAbstain MIT notice](../licenses/AgentAbstain-MIT.txt). The dataset retains its own license and notices on Hugging Face. A blanket license for all original project code has not yet been assigned.
