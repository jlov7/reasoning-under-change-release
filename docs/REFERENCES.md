# References and provenance

Yule Wen, Yixin Ye, Yanzhe Zhang, Diyi Yang and Hao Zhu. **Real-Time Reasoning
Agents in Evolving Environments**. arXiv:2511.04898v1, submitted 7 November 2025.
[Primary paper](https://arxiv.org/abs/2511.04898). Title/version checked against
the arXiv record on 20 October 2026. The paper introduces Real-Time Reasoning
Gym and AgileThinker; neither is reproduced or bundled here.

This repository's simulator, scenarios, static task generator and result records
are project-authored local research artifacts. No third-party paper text,
benchmark corpus, pretrained model or model output is redistributed. The
original concepts are credited; this package makes no first-invention claim.

Original project code is distributed under [MIT](../LICENSE). No third-party
source code is bundled.

The core has no runtime dependencies. Build reproduction pins setuptools,
wheel and packaging separately in `requirements-build.txt`; those tools are
not vendored into the wheel. Their own distributions retain their licences.

The retained planning reports include only host platform, Python version,
clock properties, task inputs and complete measurements. They contain no local
account paths, credentials or participant data. The bundled copies preserve the
historical JSON bytes. `data/SHA256.json` gives each resource's byte SHA-256;
canonical task/manifest hashes inside records use sorted JSON keys and compact
separators. These are distinct hashes with distinct purposes.
