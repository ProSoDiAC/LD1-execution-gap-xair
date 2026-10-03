# XAIR — eXecution-time Action Intent Runtime

Reference implementation, experiment suites, and frozen data for the paper
*Execution-Time Validation of Context-Carrying Action Intents in Industrial
Cyber-Physical Systems* (D'Agati, Tricomi, Mirto, Merlino).

XAIR validates **AIS action intents** (JSON Schema in `schemas/`) against a
versioned plant-context snapshot at validation time `t_v`. Before the
middleware release at `t_m`, the actuator gateway either rechecks the versions
of the context the intent depends on at `t_g` (optimistic), asks XAIR for an
atomic authorization commit at `t_c` serialized with every context update, or
releases through a command that the controller owning the context executes at
`t_a` only if its own state is unchanged.

## Layout

```
xair/            runtime package (core: validation, lifecycle FSM, context store; adapters: FastAPI server)
schemas/         AIS v1 JSON Schema
examples/        example intents
tests/           unit tests (+ opt-in HTTP integration tests)
scripts/         stack start/stop, actuator gateway, ROS witness, campaign and verification scripts
experiments/     one script per suite (run_e*.py), aggregation, figures, EVALUATION.md
simulation/      Gazebo industrial cell (E8) and OPC UA bridge (E15)
docker/          ROS 2 Jazzy/Gazebo image (E8), distributed-testbed compose file, OPA policy, OpenPLC program (E24)
data/execution-gap/   frozen per-trial data behind every number in the paper
docs/detailed-results/ protocols, per-condition tables, and detailed results (PDF + LaTeX source),
                 regenerated from data/execution-gap/ by scripts/build_detailed_results.sh
journal/         manuscript — local only, git-ignored, never published
```

`experiments/results/` is the scratch output of any run (git-ignored);
`scripts/sync_paper_outputs.sh` freezes a complete campaign into `data/execution-gap/`.
The detailed-results document, [`docs/detailed-results/detailed_results.pdf`](docs/detailed-results/detailed_results.pdf),
gives the protocols, per-condition tables, and full results behind the paper's evaluation.

## Quick start

```bash
./scripts/ensure_venv.sh          # .venv (Python >= 3.12, via uv if present) + install -e ".[dev]"
.venv/bin/python -m pytest -q tests
./scripts/start_full_stack.sh     # Redis (docker) + XAIR :8080 + gateway :9092
./scripts/verify_e2e.sh
./scripts/stop_full_stack.sh
```

Ports and endpoints are configurable (`XAIR_PORT`, `ADAPTER_HTTP_PORT`,
`ADAPTER_WS_PORT`, `REDIS_URL`; empty `REDIS_URL` = in-memory store). Example
for a shared host:

```bash
XAIR_PORT=18080 ADAPTER_HTTP_PORT=19092 ADAPTER_WS_PORT=19091 \
REDIS_URL=redis://127.0.0.1:6379/1 ./scripts/run_paper_campaign.sh
```

## Reproducing the paper

| Step | Command | Output |
|------|---------|--------|
| Single-host HTTP campaign (E0–E4, E9–E14, ~25 min) | `./scripts/run_paper_campaign.sh` | `experiments/results/` |
| Freeze + summary + figures + generated tables + detailed-results document | `./scripts/sync_paper_outputs.sh` | `data/execution-gap/`, `docs/detailed-results/` |
| E8-Gazebo in container (ROS 2 Jazzy + Gazebo Harmonic) | `./scripts/run_e8_docker.sh 30 <tag>` | `experiments/results/e8_gazebo_campaign<tag>.csv` |
| E8-Gazebo on a native Jazzy host | `./scripts/run_e8_gazebo_full.sh 30 <tag>` | same |
| E6 netem in a network namespace (root) | `sudo ./scripts/run_e6_netns.sh 30 10 500` | `experiments/results/e6_network.csv` |
| E4/E12 on reserved cores + dedicated Redis | `./scripts/run_pinned_perf.sh` | `experiments/results/pinned/` |
| Distributed testbed (Redis, OPA, XAIR, cell controller, gateway, producers in containers; netem delay; root) | `sudo ./scripts/run_distributed.sh 0.5ms 0.1ms` | `experiments/results/distributed/` |
| Physical remote node (gateway + cell controller on another machine) | `PHYS_HOST=user@host ./scripts/run_physical.sh` | `experiments/results/physical/` |
| Physical nodes on one LAN (XAIR + producers on one machine, gateway + controller on another) | `CORE_HOST=.. CORE_IP=.. EDGE_HOST=.. EDGE_IP=.. ./scripts/run_lan.sh` | `experiments/results/lan/` |
| E23 predicate-truth scaling (0–10 000 predicates, document vs hash layout; docker) | `cd experiments && python run_e23_truth_scaling.py` | `experiments/results/scaling/` |
| E24 conditional actuation on OpenPLC v3 (image built from an OpenPLC_v3 checkout as `xair-openplc`) | `./scripts/run_openplc.sh` | `experiments/results/openplc/` |
| E25 on an embedded board (Raspberry Pi 4 / VisionFive 2 over ssh; uv on the board) | `BOARD=rpi4 ./scripts/run_embedded.sh` (`REDIS_BIN=...` where no docker) | `experiments/results/embedded/<board>/` |
| E20 production-log prevalence (offline part only) | `cd experiments && python run_e20_mes.py --offline-only` | `experiments/results/` |
| All distributed campaigns (5 repetitions + sensitivity; ~4 h) | `sudo ./scripts/run_distributed_campaigns.sh` | `experiments/results/distributed/{,campaigns,sensitivity}` |
| E15 OPC UA (needs `asyncua`) | `.venv/bin/python experiments/run_e15_opcua_hil.py --runs 30` | `experiments/results/e15_opcua_hil.csv` |
| Smoke check (scratch dir) | `./scripts/verify_reproduction.sh` | temp dir |
| Artifact check (frozen dataset complete, tests) | `./scripts/verify_artifact.sh` | — |
| Clean-clone audit (fresh clone, venv, tests, smoke) | `./scripts/clean_clone_audit.sh` | temp dir |

Suite definitions, metrics, and data provenance are in
[experiments/EVALUATION.md](experiments/EVALUATION.md).

## Repository

The repository is published at <https://github.com/ProSoDiAC/LD1-execution-gap-xair>
as a single snapshot of the code, data, and documents behind the paper.
`journal/` (the manuscript) is in `.gitignore`, and `clean_clone_audit.sh`
fails if it is ever tracked.

## License

Apache-2.0 — see [LICENSE](LICENSE). Citation metadata: [CITATION.cff](CITATION.cff).
