# HERA

**Harness–Environment Co-Evolution for Reliable Agentic Abstention**

Han Luo*, Bingbing Wen*, Guang Yang, Zora Zhiruo Wang, Pan Lu, and Lucy Lu Wang.

\* Equal contribution.

This repository hosts the HERA project page, accompanying paper, and an interactive example of a matched feasible–infeasible task pair.

- Project page: <https://lhannnn.github.io/HERA-Bench/>
- Paper: [paper.pdf](paper.pdf)
- Citation: [citation.bib](citation.bib)

## Website

The website is a static page with no build dependencies. GitHub Pages should publish from `main` at the repository root. All asset paths are relative so the site works under `/HERA-Bench/`.

For a local preview, serve this directory with any static HTTP server.

## Content provenance

- Results and task details follow the supplied September 26, 2026 manuscript.
- The method illustration is extracted from Figure 1.
- The weather example follows Figures 7 and 8 and uses a frozen snapshot; it does not fetch live weather.
- Baselines, transfer results, and the cost comparison follow Tables 1 and 3 and Section 5.2. Training-pool results are identified separately.
- Author affiliations follow the author-provided list, with Guang Yang affiliated only with the University of Washington.
- Han Luo and Bingbing Wen are marked as equal contributors. Contribution markers are display information and are not part of the BibTeX author names.

The files in this initial website release do not include the executable benchmark dataset or harness implementation.

## Citation

```bibtex
@misc{luo2026hera,
  title  = {HERA: Harness--Environment Co-Evolution for Reliable Agentic Abstention},
  author = {Luo, Han and Wen, Bingbing and Yang, Guang and Wang, Zora Zhiruo and Lu, Pan and Wang, Lucy Lu},
  year   = {2026},
  url    = {https://lhannnn.github.io/HERA-Bench/}
}
```
