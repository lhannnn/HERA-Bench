# HERA

**Harness–Environment Co-Evolution for Reliable Agentic Abstention**

[Project page](https://lhannnn.github.io/HERA-Bench/) · [Dataset](https://huggingface.co/datasets/sxcn/HERA-Bench) · [Citation](citation.bib)

HERA-Bench contains 60 matched feasible–infeasible task pairs (120 instances). This repository provides the base and final harnesses and a shared runner. Tasks, executable environments, and grading are hosted on Hugging Face.

## Setup

Python 3.12:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
hf auth login  # Required while the dataset is private
python -m hera_runner download
python -m hera_runner verify
```

## Run

Set your API key in `OPENAI_API_KEY`. Copy `configs/model.example.json` to `configs/model.json` and set your model ID. Running a model makes API calls.

```bash
python -m hera_runner run --harness base --config configs/model.json \
  --pairs 1 --output runs/base-pair1
python -m hera_runner run --harness final --config configs/model.json \
  --pairs 1 --output runs/final-pair1
python -m hera_runner score runs/final-pair1
```

Use `--pairs all` for all 60 pairs. Both variants are evaluated automatically. See [reproduction details](docs/reproduction.md) for settings, output files, validation, and the evaluation boundary.

## Citation

See [citation.bib](citation.bib). Updated paper arXiv identifier: **[ARXIV_ID_PENDING]**.
