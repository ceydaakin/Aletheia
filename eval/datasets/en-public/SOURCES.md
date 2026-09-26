# EN-Public corpus — sources and licences

PRD §7.1: *Wikipedia subset + arXiv abstracts*, chosen for comparability with
published work. Built by `scripts/fetch_en_public.py`; pinned ids are in
`manifest.json`. Everything under `corpus/` is ingested; this file is not.

- Retrieval date: **2026-09-26**
- Wikipedia articles: 30 (three clusters of near-duplicate distractors)
- arXiv abstracts: 120 (five topics, deduplicated across topics)

## Licences

**Wikipedia.** Article text is from English Wikipedia contributors and is
licensed under the [Creative Commons Attribution-ShareAlike 4.0 International
Licence (CC BY-SA 4.0)](https://creativecommons.org/licenses/by-sa/4.0/). The
text was **modified**: fetched as plain text through the MediaWiki TextExtracts
API (`prop=extracts&explaintext=1`), which omits tables, infoboxes, images and
citations; converted to Markdown (section headings to `##`/`###`/`####`); back
matter (See also, References, External links, Further reading, Notes and
similar) removed; empty sections removed; articles longer than
40,000 characters truncated at a section boundary. The derived
files are shared under the same CC BY-SA 4.0 licence. Authorship: see each
article's history page on Wikipedia (the permanent links below identify the
exact revision used).

**arXiv.** Only titles, author names and abstracts are included — no e-print
(PDF/source) content. Per the [arXiv API Terms of
Use](https://info.arxiv.org/help/api/tou.html), descriptive metadata — which
arXiv defines as including title, abstract, authors, identifiers and
classification terms — is available under the [CC0 1.0 Public Domain
Dedication](https://creativecommons.org/publicdomain/zero/1.0/). Abstract
whitespace was normalised. Thank you to arXiv for use of its open access
interoperability.

## Wikipedia articles

| File | Cluster | Article (permanent link) | oldid | Revision timestamp | Chars | Truncated |
|---|---|---|---|---|---|---|
| `wiki-general-data-protection-regulation.md` | data-protection | [General Data Protection Regulation](https://en.wikipedia.org/w/index.php?title=General_Data_Protection_Regulation&oldid=1370738186) | 1370738186 | 2026-08-22T21:00:16Z | 39727 | yes |
| `wiki-california-consumer-privacy-act.md` | data-protection | [California Consumer Privacy Act](https://en.wikipedia.org/w/index.php?title=California_Consumer_Privacy_Act&oldid=1371997091) | 1371997091 | 2026-08-29T19:43:54Z | 9424 | no |
| `wiki-data-protection-act-2018.md` | data-protection | [Data Protection Act 2018](https://en.wikipedia.org/w/index.php?title=Data_Protection_Act_2018&oldid=1363982878) | 1363982878 | 2026-07-13T18:57:28Z | 5467 | no |
| `wiki-eprivacy-directive.md` | data-protection | [EPrivacy Directive](https://en.wikipedia.org/w/index.php?title=EPrivacy_Directive&oldid=1363683125) | 1363683125 | 2026-07-11T17:57:41Z | 8319 | no |
| `wiki-max-schrems.md` | data-protection | [Max Schrems](https://en.wikipedia.org/w/index.php?title=Max_Schrems&oldid=1357085300) | 1357085300 | 2026-05-31T17:43:14Z | 16284 | no |
| `wiki-eu-us-privacy-shield.md` | data-protection | [EU–US Privacy Shield](https://en.wikipedia.org/w/index.php?title=EU%E2%80%93US_Privacy_Shield&oldid=1357740634) | 1357740634 | 2026-06-04T11:57:41Z | 8450 | no |
| `wiki-health-insurance-portability-and-accountability-act.md` | data-protection | [Health Insurance Portability and Accountability Act](https://en.wikipedia.org/w/index.php?title=Health_Insurance_Portability_and_Accountability_Act&oldid=1373586207) | 1373586207 | 2026-09-06T19:46:24Z | 38926 | yes |
| `wiki-personal-information-protection-and-electronic-documents-act.md` | data-protection | [Personal Information Protection and Electronic Documents Act](https://en.wikipedia.org/w/index.php?title=Personal_Information_Protection_and_Electronic_Documents_Act&oldid=1363697237) | 1363697237 | 2026-07-11T19:48:38Z | 10028 | no |
| `wiki-general-personal-data-protection-law.md` | data-protection | [General Personal Data Protection Law](https://en.wikipedia.org/w/index.php?title=General_Personal_Data_Protection_Law&oldid=1328284711) | 1328284711 | 2025-12-18T23:36:34Z | 5047 | no |
| `wiki-personal-information-protection-law-of-the-peoples-republic-of-china.md` | data-protection | [Personal Information Protection Law of the People's Republic of China](https://en.wikipedia.org/w/index.php?title=Personal_Information_Protection_Law_of_the_People%27s_Republic_of_China&oldid=1358718978) | 1358718978 | 2026-06-10T14:11:19Z | 19218 | no |
| `wiki-voyager-1.md` | space-missions | [Voyager 1](https://en.wikipedia.org/w/index.php?title=Voyager_1&oldid=1376474466) | 1376474466 | 2026-09-24T10:17:03Z | 33140 | no |
| `wiki-hubble-space-telescope.md` | space-missions | [Hubble Space Telescope](https://en.wikipedia.org/w/index.php?title=Hubble_Space_Telescope&oldid=1373741132) | 1373741132 | 2026-09-07T17:48:19Z | 39272 | yes |
| `wiki-james-webb-space-telescope.md` | space-missions | [James Webb Space Telescope](https://en.wikipedia.org/w/index.php?title=James_Webb_Space_Telescope&oldid=1376832017) | 1376832017 | 2026-09-26T14:13:40Z | 37460 | yes |
| `wiki-apollo-11.md` | space-missions | [Apollo 11](https://en.wikipedia.org/w/index.php?title=Apollo_11&oldid=1371120273) | 1371120273 | 2026-08-24T18:22:02Z | 36154 | yes |
| `wiki-cassini-huygens.md` | space-missions | [Cassini–Huygens](https://en.wikipedia.org/w/index.php?title=Cassini%E2%80%93Huygens&oldid=1368808017) | 1368808017 | 2026-08-11T05:59:44Z | 39862 | yes |
| `wiki-rosetta-spacecraft.md` | space-missions | [Rosetta (spacecraft)](https://en.wikipedia.org/w/index.php?title=Rosetta_%28spacecraft%29&oldid=1372319916) | 1372319916 | 2026-08-31T06:58:19Z | 38147 | no |
| `wiki-new-horizons.md` | space-missions | [New Horizons](https://en.wikipedia.org/w/index.php?title=New_Horizons&oldid=1375345755) | 1375345755 | 2026-09-17T07:04:04Z | 37592 | yes |
| `wiki-international-space-station.md` | space-missions | [International Space Station](https://en.wikipedia.org/w/index.php?title=International_Space_Station&oldid=1376269097) | 1376269097 | 2026-09-23T03:22:49Z | 39521 | yes |
| `wiki-mars-science-laboratory.md` | space-missions | [Mars Science Laboratory](https://en.wikipedia.org/w/index.php?title=Mars_Science_Laboratory&oldid=1371669736) | 1371669736 | 2026-08-27T19:37:59Z | 32629 | no |
| `wiki-chandrayaan-3.md` | space-missions | [Chandrayaan-3](https://en.wikipedia.org/w/index.php?title=Chandrayaan-3&oldid=1376171981) | 1376171981 | 2026-09-22T14:30:44Z | 29213 | no |
| `wiki-golden-gate-bridge.md` | bridges | [Golden Gate Bridge](https://en.wikipedia.org/w/index.php?title=Golden_Gate_Bridge&oldid=1373898858) | 1373898858 | 2026-09-08T16:31:16Z | 37911 | yes |
| `wiki-bosphorus-bridge.md` | bridges | [Bosphorus Bridge](https://en.wikipedia.org/w/index.php?title=Bosphorus_Bridge&oldid=1372236035) | 1372236035 | 2026-08-30T23:47:33Z | 9033 | no |
| `wiki-1915-canakkale-bridge.md` | bridges | [1915 Çanakkale Bridge](https://en.wikipedia.org/w/index.php?title=1915_%C3%87anakkale_Bridge&oldid=1371498446) | 1371498446 | 2026-08-26T20:34:02Z | 4448 | no |
| `wiki-akashi-kaikyo-bridge.md` | bridges | [Akashi Kaikyo Bridge](https://en.wikipedia.org/w/index.php?title=Akashi_Kaikyo_Bridge&oldid=1354335174) | 1354335174 | 2026-05-15T19:57:42Z | 6846 | no |
| `wiki-millau-viaduct.md` | bridges | [Millau Viaduct](https://en.wikipedia.org/w/index.php?title=Millau_Viaduct&oldid=1376363955) | 1376363955 | 2026-09-23T18:39:13Z | 26684 | no |
| `wiki-brooklyn-bridge.md` | bridges | [Brooklyn Bridge](https://en.wikipedia.org/w/index.php?title=Brooklyn_Bridge&oldid=1372755997) | 1372755997 | 2026-09-02T03:35:31Z | 38743 | yes |
| `wiki-tower-bridge.md` | bridges | [Tower Bridge](https://en.wikipedia.org/w/index.php?title=Tower_Bridge&oldid=1376397860) | 1376397860 | 2026-09-23T23:31:27Z | 32468 | no |
| `wiki-oresund-bridge.md` | bridges | [Øresund Bridge](https://en.wikipedia.org/w/index.php?title=%C3%98resund_Bridge&oldid=1373245078) | 1373245078 | 2026-09-04T20:40:22Z | 35107 | no |
| `wiki-sydney-harbour-bridge.md` | bridges | [Sydney Harbour Bridge](https://en.wikipedia.org/w/index.php?title=Sydney_Harbour_Bridge&oldid=1374390607) | 1374390607 | 2026-09-11T19:42:04Z | 39473 | yes |
| `wiki-hong-kong-zhuhai-macau-bridge.md` | bridges | [Hong Kong–Zhuhai–Macau Bridge](https://en.wikipedia.org/w/index.php?title=Hong_Kong%E2%80%93Zhuhai%E2%80%93Macau_Bridge&oldid=1372967677) | 1372967677 | 2026-09-03T06:19:16Z | 16190 | no |

## arXiv abstracts

| File | Topic | Title | First author, year | Source |
|---|---|---|---|---|
| `arxiv-2106-08460.md` | conformal prediction | Localized Conformal Prediction: A Generalized Inference Framework for Conformal Prediction | Leying Guan, 2021 | [arXiv:2106.08460](https://arxiv.org/abs/2106.08460v2) |
| `arxiv-1908-08558.md` | conformal prediction | Conformal prediction with localization | Leying Guan, 2019 | [arXiv:1908.08558](https://arxiv.org/abs/1908.08558v3) |
| `arxiv-2103-00627.md` | conformal prediction | Multi Split Conformal Prediction | Aldo Solari, 2021 | [arXiv:2103.00627](https://arxiv.org/abs/2103.00627v2) |
| `arxiv-2607-16675.md` | conformal prediction | Isotonic Conformal Prediction | Daniel Bensimon, 2026 | [arXiv:2607.16675](https://arxiv.org/abs/2607.16675v1) |
| `arxiv-2210-00173.md` | conformal prediction | Predictive Inference with Feature Conformal Prediction | Jiaye Teng, 2022 | [arXiv:2210.00173](https://arxiv.org/abs/2210.00173v4) |
| `arxiv-2504-02292.md` | conformal prediction | Unifying Different Theories of Conformal Prediction | Rina Foygel Barber, 2025 | [arXiv:2504.02292](https://arxiv.org/abs/2504.02292v2) |
| `arxiv-0706-3188.md` | conformal prediction | A tutorial on conformal prediction | Glenn Shafer, 2007 | [arXiv:0706.3188](https://arxiv.org/abs/0706.3188v1) |
| `arxiv-2509-24095.md` | conformal prediction | Singleton-Optimized Conformal Prediction | Tao Wang, 2025 | [arXiv:2509.24095](https://arxiv.org/abs/2509.24095v2) |
| `arxiv-2402-07307.md` | conformal prediction | Self-Calibrating Conformal Prediction | Lars van der Laan, 2024 | [arXiv:2402.07307](https://arxiv.org/abs/2402.07307v3) |
| `arxiv-2303-01422.md` | conformal prediction | Design-based conformal prediction | Jerzy Wieczorek, 2023 | [arXiv:2303.01422](https://arxiv.org/abs/2303.01422v2) |
| `arxiv-2402-04344.md` | conformal prediction | Does confidence calibration improve conformal prediction? | Huajun Xi, 2024 | [arXiv:2402.04344](https://arxiv.org/abs/2402.04344v3) |
| `arxiv-2005-06095.md` | conformal prediction | Exchangeability, Conformal Prediction, and Rank Tests | Arun Kumar Kuchibhotla, 2020 | [arXiv:2005.06095](https://arxiv.org/abs/2005.06095v3) |
| `arxiv-2410-19653.md` | conformal prediction | Conformal Prediction for Multimodal Regression | Alexis Bose, 2024 | [arXiv:2410.19653](https://arxiv.org/abs/2410.19653v3) |
| `arxiv-2411-01596.md` | conformal prediction | Strategic Conformal Prediction | Daniel Csillag, 2024 | [arXiv:2411.01596](https://arxiv.org/abs/2411.01596v1) |
| `arxiv-2401-13744.md` | conformal prediction | Conformal Prediction Sets Improve Human Decision Making | Jesse C. Cresswell, 2024 | [arXiv:2401.13744](https://arxiv.org/abs/2401.13744v3) |
| `arxiv-2603-23923.md` | conformal prediction | Elements of Conformal Prediction | Matteo Sesia, 2026 | [arXiv:2603.23923](https://arxiv.org/abs/2603.23923v2) |
| `arxiv-2503-23561.md` | conformal prediction | Bridging conformal prediction and scenario optimization | Niall O'Sullivan, 2025 | [arXiv:2503.23561](https://arxiv.org/abs/2503.23561v2) |
| `arxiv-1904-06019.md` | conformal prediction | Conformal Prediction Under Covariate Shift | Ryan J. Tibshirani, 2019 | [arXiv:1904.06019](https://arxiv.org/abs/1904.06019v3) |
| `arxiv-2510-10324.md` | conformal prediction | On some practical challenges of conformal prediction | Liang Hong, 2025 | [arXiv:2510.10324](https://arxiv.org/abs/2510.10324v2) |
| `arxiv-1611-09933.md` | conformal prediction | Trimmed Conformal Prediction for High-Dimensional Models | Wenyu Chen, 2016 | [arXiv:1611.09933](https://arxiv.org/abs/1611.09933v1) |
| `arxiv-2504-12582.md` | conformal prediction | Fair Conformal Prediction for Incomplete Covariate Data | Jingsen Kong, 2025 | [arXiv:2504.12582](https://arxiv.org/abs/2504.12582v2) |
| `arxiv-2407-07700.md` | conformal prediction | Split Conformal Prediction under Data Contamination | Jase Clarkson, 2024 | [arXiv:2407.07700](https://arxiv.org/abs/2407.07700v3) |
| `arxiv-2503-11709.md` | conformal prediction | Conformal Prediction and Human Decision Making | Jessica Hullman, 2025 | [arXiv:2503.11709](https://arxiv.org/abs/2503.11709v2) |
| `arxiv-2501-14544.md` | conformal prediction | Distributed Conformal Prediction via Message Passing | Haifeng Wen, 2025 | [arXiv:2501.14544](https://arxiv.org/abs/2501.14544v2) |
| `arxiv-2305-06983.md` | retrieval-augmented generation | Active Retrieval Augmented Generation | Zhengbao Jiang, 2023 | [arXiv:2305.06983](https://arxiv.org/abs/2305.06983v2) |
| `arxiv-2405-13002.md` | retrieval-augmented generation | DuetRAG: Collaborative Retrieval-Augmented Generation | Dian Jiao, 2024 | [arXiv:2405.13002](https://arxiv.org/abs/2405.13002v1) |
| `arxiv-2505-00443.md` | retrieval-augmented generation | Distributed Retrieval-Augmented Generation | Chenhao Xu, 2025 | [arXiv:2505.00443](https://arxiv.org/abs/2505.00443v1) |
| `arxiv-2405-16506.md` | retrieval-augmented generation | GRAG: Graph Retrieval-Augmented Generation | Yuntong Hu, 2024 | [arXiv:2405.16506](https://arxiv.org/abs/2405.16506v3) |
| `arxiv-2410-11321.md` | retrieval-augmented generation | Self-adaptive Multimodal Retrieval-Augmented Generation | Wenjia Zhai, 2024 | [arXiv:2410.11321](https://arxiv.org/abs/2410.11321v1) |
| `arxiv-2309-01431.md` | retrieval-augmented generation | Benchmarking Large Language Models in Retrieval-Augmented Generation | Jiawei Chen, 2023 | [arXiv:2309.01431](https://arxiv.org/abs/2309.01431v2) |
| `arxiv-2401-15884.md` | retrieval-augmented generation | Corrective Retrieval Augmented Generation | Shi-Qi Yan, 2024 | [arXiv:2401.15884](https://arxiv.org/abs/2401.15884v3) |
| `arxiv-2407-03955.md` | retrieval-augmented generation | Meta-prompting Optimized Retrieval-augmented Generation | João Rodrigues, 2024 | [arXiv:2407.03955](https://arxiv.org/abs/2407.03955v1) |
| `arxiv-2504-08748.md` | retrieval-augmented generation | A Survey of Multimodal Retrieval-Augmented Generation | Lang Mei, 2025 | [arXiv:2504.08748](https://arxiv.org/abs/2504.08748v1) |
| `arxiv-2406-03790.md` | retrieval-augmented generation | End-to-End Trainable Retrieval-Augmented Generation for Relation Extraction | Kohei Makino, 2024 | [arXiv:2406.03790](https://arxiv.org/abs/2406.03790v2) |
| `arxiv-2604-18509.md` | retrieval-augmented generation | MASS-RAG: Multi-Agent Synthesis Retrieval-Augmented Generation | Xingchen Xiao, 2026 | [arXiv:2604.18509](https://arxiv.org/abs/2604.18509v2) |
| `arxiv-2402-13178.md` | retrieval-augmented generation | Benchmarking Retrieval-Augmented Generation for Medicine | Guangzhi Xiong, 2024 | [arXiv:2402.13178](https://arxiv.org/abs/2402.13178v2) |
| `arxiv-2406-13692.md` | retrieval-augmented generation | Synchronous Faithfulness Monitoring for Trustworthy Retrieval-Augmented Generation | Di Wu, 2024 | [arXiv:2406.13692](https://arxiv.org/abs/2406.13692v2) |
| `arxiv-2502-06864.md` | retrieval-augmented generation | Knowledge Graph-Guided Retrieval Augmented Generation | Xiangrong Zhu, 2025 | [arXiv:2502.06864](https://arxiv.org/abs/2502.06864v1) |
| `arxiv-2501-15915.md` | retrieval-augmented generation | Parametric Retrieval Augmented Generation | Weihang Su, 2025 | [arXiv:2501.15915](https://arxiv.org/abs/2501.15915v1) |
| `arxiv-2502-01113.md` | retrieval-augmented generation | GFM-RAG: Graph Foundation Model for Retrieval Augmented Generation | Linhao Luo, 2025 | [arXiv:2502.01113](https://arxiv.org/abs/2502.01113v3) |
| `arxiv-2508-15253.md` | retrieval-augmented generation | Conflict-Aware Soft Prompting for Retrieval-Augmented Generation | Eunseong Choi, 2025 | [arXiv:2508.15253](https://arxiv.org/abs/2508.15253v2) |
| `arxiv-2501-11929.md` | retrieval-augmented generation | ALoFTRAG: Automatic Local Fine Tuning for Retrieval Augmented Generation | Peter Devine, 2025 | [arXiv:2501.11929](https://arxiv.org/abs/2501.11929v1) |
| `arxiv-2412-14457.md` | retrieval-augmented generation | VISA: Retrieval Augmented Generation with Visual Source Attribution | Xueguang Ma, 2024 | [arXiv:2412.14457](https://arxiv.org/abs/2412.14457v1) |
| `arxiv-2505-18906.md` | retrieval-augmented generation | Federated Retrieval-Augmented Generation: A Systematic Mapping Study | Abhijit Chakraborty, 2025 | [arXiv:2505.18906](https://arxiv.org/abs/2505.18906v2) |
| `arxiv-2601-16503.md` | retrieval-augmented generation | MRAG: Benchmarking Retrieval-Augmented Generation for Bio-medicine | Liz Li, 2026 | [arXiv:2601.16503](https://arxiv.org/abs/2601.16503v2) |
| `arxiv-2512-17194.md` | retrieval-augmented generation | MMRAG-RFT: Two-stage Reinforcement Fine-tuning for Explainable Multi-modal Retrieval-augmented Generation | Shengwei Zhao, 2025 | [arXiv:2512.17194](https://arxiv.org/abs/2512.17194v1) |
| `arxiv-2607-01852.md` | retrieval-augmented generation | Evaluating Chunking Strategies for Retrieval-Augmented Generation on Academic Texts | Valentin J. J. Kreileder, 2026 | [arXiv:2607.01852](https://arxiv.org/abs/2607.01852v1) |
| `arxiv-2412-15404.md` | retrieval-augmented generation | A Retrieval-Augmented Generation Framework for Academic Literature Navigation in Data Science | Ahmet Yasin Aytar, 2024 | [arXiv:2412.15404](https://arxiv.org/abs/2412.15404v1) |
| `arxiv-2605-26366.md` | hallucination detection | Automatic Layer Selection for Hallucination Detection | Xinpeng Wang, 2026 | [arXiv:2605.26366](https://arxiv.org/abs/2605.26366v3) |
| `arxiv-2509-11536.md` | hallucination detection | HARP: Hallucination Detection via Reasoning Subspace Projection | Junjie Hu, 2025 | [arXiv:2509.11536](https://arxiv.org/abs/2509.11536v2) |
| `arxiv-2507-20546.md` | hallucination detection | Enhancing Hallucination Detection via Future Context | Joosung Lee, 2025 | [arXiv:2507.20546](https://arxiv.org/abs/2507.20546v2) |
| `arxiv-2504-08596.md` | hallucination detection | MedHal: An Evaluation Dataset for Medical Hallucination Detection | Gaya Mehenni, 2025 | [arXiv:2504.08596](https://arxiv.org/abs/2504.08596v2) |
| `arxiv-2407-15975.md` | hallucination detection | Multilingual Fine-Grained News Headline Hallucination Detection | Jiaming Shen, 2024 | [arXiv:2407.15975](https://arxiv.org/abs/2407.15975v1) |
| `arxiv-2510-15977.md` | hallucination detection | Bolster Hallucination Detection via Prompt-Guided Data Augmentation | Wenyun Li, 2025 | [arXiv:2510.15977](https://arxiv.org/abs/2510.15977v1) |
| `arxiv-2402-03190.md` | hallucination detection | Unified Hallucination Detection for Multimodal Large Language Models | Xiang Chen, 2024 | [arXiv:2402.03190](https://arxiv.org/abs/2402.03190v4) |
| `arxiv-2504-17004.md` | hallucination detection | (Im)possibility of Automated Hallucination Detection in Large Language Models | Amin Karbasi, 2025 | [arXiv:2504.17004](https://arxiv.org/abs/2504.17004v2) |
| `arxiv-2602-07253.md` | hallucination detection | From Out-of-Distribution Detection to Hallucination Detection: A Geometric View | Litian Liu, 2026 | [arXiv:2602.07253](https://arxiv.org/abs/2602.07253v4) |
| `arxiv-2402-10496.md` | hallucination detection | Comparing Hallucination Detection Metrics for Multilingual Generation | Haoqiang Kang, 2024 | [arXiv:2402.10496](https://arxiv.org/abs/2402.10496v2) |
| `arxiv-2509-23580.md` | hallucination detection | LLM Hallucination Detection: HSAD | JinXin Li, 2025 | [arXiv:2509.23580](https://arxiv.org/abs/2509.23580v2) |
| `arxiv-2601-19245.md` | hallucination detection | Beyond In-Domain Detection: SpikeScore for Cross-Domain Hallucination Detection | Yongxin Deng, 2026 | [arXiv:2601.19245](https://arxiv.org/abs/2601.19245v5) |
| `arxiv-2504-07863.md` | hallucination detection | Robust Hallucination Detection in LLMs via Adaptive Token Selection | Mengjia Niu, 2025 | [arXiv:2504.07863](https://arxiv.org/abs/2504.07863v3) |
| `arxiv-2506-09886.md` | hallucination detection | Probabilistic distances-based hallucination detection in LLMs with RAG | Rodion Oblovatny, 2025 | [arXiv:2506.09886](https://arxiv.org/abs/2506.09886v2) |
| `arxiv-2407-13702.md` | hallucination detection | ANHALTEN: Cross-Lingual Transfer for German Token-Level Reference-Free Hallucination Detection | Janek Herrlein, 2024 | [arXiv:2407.13702](https://arxiv.org/abs/2407.13702v1) |
| `arxiv-2501-02020.md` | hallucination detection | Enhancing Uncertainty Modeling with Semantic Graph for Hallucination Detection | Kedi Chen, 2025 | [arXiv:2501.02020](https://arxiv.org/abs/2501.02020v3) |
| `arxiv-2510-19318.md` | hallucination detection | HAD: HAllucination Detection Language Models Based on a Comprehensive Hallucination Taxonomy | Fan Xu, 2025 | [arXiv:2510.19318](https://arxiv.org/abs/2510.19318v1) |
| `arxiv-2502-17598.md` | hallucination detection | Hallucination Detection in LLMs Using Spectral Features of Attention Maps | Jakub Binkowski, 2025 | [arXiv:2502.17598](https://arxiv.org/abs/2502.17598v2) |
| `arxiv-2606-06959.md` | hallucination detection | OpenHalDet: A Unified Benchmark for Hallucination Detection across Diverse Generation Scenarios | Xinyi Li, 2026 | [arXiv:2606.06959](https://arxiv.org/abs/2606.06959v1) |
| `arxiv-2510-01274.md` | hallucination detection | TraceDet: Hallucination Detection from the Decoding Trace of Diffusion Large Language Models | Shenxu Chang, 2025 | [arXiv:2510.01274](https://arxiv.org/abs/2510.01274v1) |
| `arxiv-2502-08109.md` | hallucination detection | HuDEx: Integrating Hallucination Detection and Explainability for Enhancing the Reliability of LLM responses | Sujeong Lee, 2025 | [arXiv:2502.08109](https://arxiv.org/abs/2502.08109v1) |
| `arxiv-2509-21999.md` | hallucination detection | Black-Box Hallucination Detection via Consistency Under the Uncertain Expression | Seongho Joo, 2025 | [arXiv:2509.21999](https://arxiv.org/abs/2509.21999v1) |
| `arxiv-2503-04615.md` | hallucination detection | HalluCounter: Reference-free LLM Hallucination Detection in the Wild! | Ashok Urlana, 2025 | [arXiv:2503.04615](https://arxiv.org/abs/2503.04615v2) |
| `arxiv-2310-18344.md` | hallucination detection | Chainpoll: A high efficacy method for LLM hallucination detection | Robert Friel, 2023 | [arXiv:2310.18344](https://arxiv.org/abs/2310.18344v1) |
| `arxiv-1804-07888.md` | natural language inference | Stochastic Answer Networks for Natural Language Inference | Xiaodong Liu, 2018 | [arXiv:1804.07888](https://arxiv.org/abs/1804.07888v2) |
| `arxiv-1811-00671.md` | natural language inference | Dialogue Natural Language Inference | Sean Welleck, 2018 | [arXiv:1811.00671](https://arxiv.org/abs/1811.00671v2) |
| `arxiv-1909-03042.md` | natural language inference | Uncertain Natural Language Inference | Tongfei Chen, 2019 | [arXiv:1909.03042](https://arxiv.org/abs/1909.03042v2) |
| `arxiv-1512-08849.md` | natural language inference | Learning Natural Language Inference with LSTM | Shuohang Wang, 2015 | [arXiv:1512.08849](https://arxiv.org/abs/1512.08849v2) |
| `arxiv-1709-04348.md` | natural language inference | Natural Language Inference over Interaction Space | Yichen Gong, 2017 | [arXiv:1709.04348](https://arxiv.org/abs/1709.04348v2) |
| `arxiv-1611-04741.md` | natural language inference | A Neural Architecture Mimicking Humans End-to-End for Natural Language Inference | Biswajit Paria, 2016 | [arXiv:1611.04741](https://arxiv.org/abs/1611.04741v2) |
| `arxiv-1606-01404.md` | natural language inference | Generating Natural Language Inference Chains | Vladyslav Kolesnyk, 2016 | [arXiv:1606.01404](https://arxiv.org/abs/1606.01404v1) |
| `arxiv-2010-05444.md` | natural language inference | OCNLI: Original Chinese Natural Language Inference | Hai Hu, 2020 | [arXiv:2010.05444](https://arxiv.org/abs/2010.05444v1) |
| `arxiv-1606-01933.md` | natural language inference | A Decomposable Attention Model for Natural Language Inference | Ankur P. Parikh, 2016 | [arXiv:1606.01933](https://arxiv.org/abs/1606.01933v2) |
| `arxiv-1711-04289.md` | natural language inference | Neural Natural Language Inference Models Enhanced with External Knowledge | Qian Chen, 2017 | [arXiv:1711.04289](https://arxiv.org/abs/1711.04289v3) |
| `arxiv-2009-14539.md` | natural language inference | Case-Based Abductive Natural Language Inference | Marco Valentino, 2020 | [arXiv:2009.14539](https://arxiv.org/abs/2009.14539v4) |
| `arxiv-1803-02324.md` | natural language inference | Annotation Artifacts in Natural Language Inference Data | Suchin Gururangan, 2018 | [arXiv:1803.02324](https://arxiv.org/abs/1803.02324v2) |
| `arxiv-1508-05326.md` | natural language inference | A large annotated corpus for learning natural language inference | Samuel R. Bowman, 2015 | [arXiv:1508.05326](https://arxiv.org/abs/1508.05326v1) |
| `arxiv-2010-10501.md` | natural language inference | Natural Language Inference with Mixed Effects | William Gantt, 2020 | [arXiv:2010.10501](https://arxiv.org/abs/2010.10501v1) |
| `arxiv-1804-08207.md` | natural language inference | Collecting Diverse Natural Language Inference Problems for Sentence Representation Evaluation | Adam Poliak, 2018 | [arXiv:1804.08207](https://arxiv.org/abs/1804.08207v2) |
| `arxiv-1810-08606.md` | natural language inference | An Exploration of Dropout with RNNs for Natural Language Inference | Amit Gajbhiye, 2018 | [arXiv:1810.08606](https://arxiv.org/abs/1810.08606v1) |
| `arxiv-1909-08217.md` | natural language inference | Improving Natural Language Inference with a Pretrained Parser | Deric Pang, 2019 | [arXiv:1909.08217](https://arxiv.org/abs/1909.08217v1) |
| `arxiv-2209-06701.md` | natural language inference | Natural Language Inference Prompts for Zero-shot Emotion Classification in Text across Corpora | Flor Miriam Plaza-del-Arco, 2022 | [arXiv:2209.06701](https://arxiv.org/abs/2209.06701v2) |
| `arxiv-1607-06025.md` | natural language inference | Constructing a Natural Language Inference Dataset using Generative Neural Networks | Janez Starc, 2016 | [arXiv:1607.06025](https://arxiv.org/abs/1607.06025v2) |
| `arxiv-2205-02596.md` | natural language inference | Natural Language Inference with Self-Attention for Veracity Assessment of Pandemic Claims | M. Arana-Catania, 2022 | [arXiv:2205.02596](https://arxiv.org/abs/2205.02596v1) |
| `arxiv-1705-02364.md` | natural language inference | Supervised Learning of Universal Sentence Representations from Natural Language Inference Data | Alexis Conneau, 2017 | [arXiv:1705.02364](https://arxiv.org/abs/1705.02364v5) |
| `arxiv-1805-01042.md` | natural language inference | Hypothesis Only Baselines in Natural Language Inference | Adam Poliak, 2018 | [arXiv:1805.01042](https://arxiv.org/abs/1805.01042v1) |
| `arxiv-2005-12116.md` | natural language inference | NILE : Natural Language Inference with Faithful Natural Language Explanations | Sawan Kumar, 2020 | [arXiv:2005.12116](https://arxiv.org/abs/2005.12116v1) |
| `arxiv-2205-15550.md` | natural language inference | A Multi-level Supervised Contrastive Learning Framework for Low-Resource Natural Language Inference | Shu'ang Li, 2022 | [arXiv:2205.15550](https://arxiv.org/abs/2205.15550v1) |
| `arxiv-2402-15610.md` | selective prediction / abstention | Selective "Selective Prediction": Reducing Unnecessary Abstention in Vision-Language Reasoning | Tejas Srinivasan, 2024 | [arXiv:2402.15610](https://arxiv.org/abs/2402.15610v2) |
| `arxiv-2305-01812.md` | selective prediction / abstention | Post-Abstention: Towards Reliably Re-Attempting the Abstained Instances in QA | Neeraj Varshney, 2023 | [arXiv:2305.01812](https://arxiv.org/abs/2305.01812v1) |
| `arxiv-2205-13532.md` | selective prediction / abstention | Selective Prediction via Training Dynamics | Stephan Rabanser, 2022 | [arXiv:2205.13532](https://arxiv.org/abs/2205.13532v4) |
| `arxiv-2112-06751.md` | selective prediction / abstention | Role of Human-AI Interaction in Selective Prediction | Elizabeth Bondi, 2021 | [arXiv:2112.06751](https://arxiv.org/abs/2112.06751v2) |
| `arxiv-2008-09371.md` | selective prediction / abstention | Towards Improving Selective Prediction Ability of NLP Systems | Neeraj Varshney, 2020 | [arXiv:2008.09371](https://arxiv.org/abs/2008.09371v3) |
| `arxiv-2603-02719.md` | selective prediction / abstention | An Empirical Analysis of Calibration and Selective Prediction in Multimodal Clinical Condition Classification | L. Julián Lechuga López, 2026 | [arXiv:2603.02719](https://arxiv.org/abs/2603.02719v4) |
| `arxiv-2304-03870.md` | selective prediction / abstention | ASPEST: Bridging the Gap Between Active Learning and Selective Prediction | Jiefeng Chen, 2023 | [arXiv:2304.03870](https://arxiv.org/abs/2304.03870v3) |
| `arxiv-2310-11689.md` | selective prediction / abstention | Adaptation with Self-Evaluation to Improve Selective Prediction in LLMs | Jiefeng Chen, 2023 | [arXiv:2310.11689](https://arxiv.org/abs/2310.11689v2) |
| `arxiv-1906-05473.md` | selective prediction / abstention | Selective prediction-set models with coverage guarantees | Jean Feng, 2019 | [arXiv:1906.05473](https://arxiv.org/abs/1906.05473v2) |
| `arxiv-2111-11952.md` | selective prediction / abstention | Leveraging Selective Prediction for Reliable Image Geolocation | Apostolos Panagiotopoulos, 2021 | [arXiv:2111.11952](https://arxiv.org/abs/2111.11952v1) |
| `arxiv-2508-07617.md` | selective prediction / abstention | Selective Prediction Reduces the Negative Effects of Automation Bias Overall but Increases False Negatives | Sarah Jabbour, 2025 | [arXiv:2508.07617](https://arxiv.org/abs/2508.07617v2) |
| `arxiv-2605-01346.md` | selective prediction / abstention | CHASE: Competing Hypotheses for Ambiguity-Aware Selective Prediction | Kartik Jhawar, 2026 | [arXiv:2605.01346](https://arxiv.org/abs/2605.01346v2) |
| `arxiv-2601-22570.md` | selective prediction / abstention | Leveraging Data to Say No: Memory Augmented Plug-and-Play Selective Prediction | Aditya Sarkar, 2026 | [arXiv:2601.22570](https://arxiv.org/abs/2601.22570v1) |
| `arxiv-2203-00211.md` | selective prediction / abstention | Investigating Selective Prediction Approaches Across Several Tasks in IID, OOD, and Adversarial Settings | Neeraj Varshney, 2022 | [arXiv:2203.00211](https://arxiv.org/abs/2203.00211v1) |
| `arxiv-2604-25855.md` | selective prediction / abstention | SIEVES: Selective Prediction Generalizes through Visual Evidence Scoring | Hector G. Rodriguez, 2026 | [arXiv:2604.25855](https://arxiv.org/abs/2604.25855v2) |
| `arxiv-2509-21514.md` | selective prediction / abstention | Knowing When to Defer: Selective Prediction for Responsible Knowledge Tracing | Joshua Mitton, 2025 | [arXiv:2509.21514](https://arxiv.org/abs/2509.21514v4) |
| `arxiv-2603-21172.md` | selective prediction / abstention | Entropy Alone is Insufficient for Safe Selective Prediction in LLMs | Edward Phillips, 2026 | [arXiv:2603.21172](https://arxiv.org/abs/2603.21172v1) |
| `arxiv-2410-24029.md` | selective prediction / abstention | Joint Training for Selective Prediction | Zhaohui Li, 2024 | [arXiv:2410.24029](https://arxiv.org/abs/2410.24029v1) |
| `arxiv-2505-09591.md` | selective prediction / abstention | Variational Visual Question Answering for Uncertainty-Aware Selective Prediction | Tobias Jan Wieczorek, 2025 | [arXiv:2505.09591](https://arxiv.org/abs/2505.09591v3) |
| `arxiv-2604-17716.md` | selective prediction / abstention | Concurrent Criterion Validation of a Validity Screen for LLM Confidence Signals via Selective Prediction | Jon-Paul Cacioli, 2026 | [arXiv:2604.17716](https://arxiv.org/abs/2604.17716v1) |
| `arxiv-2402-10665.md` | selective prediction / abstention | Soft Dice Confidence: A Near-Optimal Confidence Estimator for Selective Prediction in Semantic Segmentation | Bruno Laboissiere Camargos Borges, 2024 | [arXiv:2402.10665](https://arxiv.org/abs/2402.10665v6) |
| `arxiv-2505-15008.md` | selective prediction / abstention | Know When to Abstain: Optimal Selective Classification with Likelihood Ratios | Alvin Heng, 2025 | [arXiv:2505.15008](https://arxiv.org/abs/2505.15008v3) |
| `arxiv-2510-13327.md` | selective prediction / abstention | When In Doubt, Abstain: The Impact of Abstention on Strategic Classification | Lina Alkarmi, 2025 | [arXiv:2510.13327](https://arxiv.org/abs/2510.13327v3) |
| `arxiv-2607-03528.md` | selective prediction / abstention | Aligning Language Models with Selective Prediction | Gaoxiang Luo, 2026 | [arXiv:2607.03528](https://arxiv.org/abs/2607.03528v1) |
