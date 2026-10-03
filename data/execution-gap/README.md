# Frozen dataset — execution gap / AIS / XAIR

Per-trial data behind every number, table, and figure of the paper and of
`docs/detailed-results/`. The top-level
files were produced on 2026-09-23 on one host (see `environment.txt`; the host was shared,
load average 68–75). Seven sub-directories hold the later campaigns (2026-09-24 to 2026-10-02;
the distributed, physical, LAN, OpenPLC, and embedded subsets were rerun on 2026-10-02 with the current code):

- `pinned/`: E4 (three runs) and E12 on reserved cores with a dedicated Redis
  (`scripts/run_pinned_perf.sh`);
- `distributed/`: campaign 1 on the container testbed (Redis, policy engine, XAIR, gateway,
  OPC UA cell controller, producers; shaped delay): E1 (with the policy engine), E1c, E9,
  E10 (optimistic and atomic commit, induced and natural window, shared-clock positions),
  E10-deadline (four release paths), E10-ABA, E12 (with the policy engine), E16 and E16-trace
  (global, read-set, predicate-truth, predicate-only, policy engine), E17, E18, E18b, E20
  (4TU production log, with the decision-model and proxy sensitivity), E21 and E21-ABA (cell controller: unconditional, local interlock, version-conditional); `distributed/campaigns/c2..c5`: independent
  repetitions of E10, E10-deadline, E10-ABA, E16, E16-trace, E21, E21-ABA; `distributed/sensitivity/`:
  2 ± 1 ms delay, shared CPUs, phase-locked E16-trace. Produced by
  `scripts/run_distributed_campaigns.sh`; E18b is host-side (`experiments/run_e18b_durability.py`).
- `scaling/`: E23, cost of predicate-truth versions with 0–10 000 registered predicates,
  document vs hash layout (`experiments/run_e23_truth_scaling.py`, dedicated Redis container).
- `openplc/`: E24, conditional actuation on an OpenPLC v3 runtime over Modbus
  (`scripts/run_openplc.sh`, program `docker/openplc/cell_ctl.st`).
- `physical/`: E22, gateway and cell controller on a separate machine over a wide-area overlay
  (`scripts/run_physical.sh`): E1, E1c, E10 natural window, E10-ABA, E16, E21, E21-ABA.
- `lan/`: E22 on a Wi-Fi LAN (`scripts/run_lan.sh`): XAIR, Redis, and the producers on a Raspberry Pi 4,
  gateway and cell controller on the Apple M4 machine; same suites as `physical/`.
- `embedded/<board>/`: E25, the runtime on a Raspberry Pi 4 (`rpi4`) and a VisionFive 2 RISC-V board (`vf2`), every component on the board (`scripts/run_embedded.sh`).

Each sub-directory has its own `environment.txt`; all are frozen with
`scripts/freeze_subsets.sh`, and `paper_metrics_summary.json` summarizes them under the
keys `pinned`, `distributed`, `physical`, `lan`, `scaling`, and `openplc`. E16-trace replays the UCI "Condition monitoring of
hydraulic systems" recording (Helwig, Pignanelli, Schütze; DOI 10.24432/C5CW21, CC BY 4.0),
which is downloaded on first use and verified by SHA-256
(`24128aad2ee45eea7e6b63ebbd9992cdf25d0483a2cebefbfc13bc69079af1f2`); the recording itself
is not redistributed here. E20 uses the 4TU "Production Analysis with Process Mining
Technology" log (Levy 2014, DOI 10.4121/uuid:68726926-5ac5-4fab-b873-ee76ea412399), likewise
downloaded on first use and verified by SHA-256 (`experiments/mes_trace.py`).

| File | Suite | Produced by |
|------|-------|-------------|
| `e0_lifecycle.json` | E0 lifecycle regressions | `scripts/run_paper_campaign.sh` |
| `e1_baselines.csv`, `e1_fpr.csv` | E1 stale actuation, valid controls | idem |
| `e3_conflict_http.csv` | E3 conflict tie-break | idem |
| `e4_load_http.csv` | E4 sequential load | idem |
| `e9_consistency_sweep.csv` | E9 remote writer × cache policy | idem |
| `e10_toctou.csv`, `e10_toctou_boundary.csv` | E10 residual interval | idem |
| `e11_stratified_seed{42,7,123}.csv` | E11 mixed labels | idem |
| `e12_scaling.csv` | E12 closed-loop scaling | idem |
| `e13_faults.csv` | E13 fault cases | idem |
| `e14_variants.csv` | E14 action classes | idem |
| `e16_context_churn.csv` | E16 global vs read-set version under churn | idem |
| `e15_opcua_hil.csv` | E15 OPC UA context path | `experiments/run_e15_opcua_hil.py --runs 30` |
| `e6_network.csv`, `e6_network_fresh10s.csv` | E6 kernel impairment (500 ms / 10 s window) | `scripts/run_e6_netns.sh` |
| `e8_gazebo_campaign{1,2}.csv` | E8 Gazebo cell, two campaigns | `scripts/run_e8_docker.sh 30 <n>` |
| `paper_metrics_summary.json` | all aggregates | `experiments/aggregate_experiment_results.py` |

Code: the subsets were recorded with successive versions of this runtime, and the code in
this tree reproduces all of them. Features added after the top-level runs (atomic commit,
predicate-only and predicate-truth scopes, release-time deadline semantics, server-side
policy, extra version columns in E10) are new options whose defaults leave the recorded
behavior unchanged: in the top-level suites the ages stay far below both bounds and no
policy is installed. Features added after the `pinned/`, `distributed/`, and `physical/`
runs (hash layout, predicate retirement, parse caching, linear-time relatedness test, used
by `scaling/`, `openplc/`, and `lan/`) change latency, not outcomes.
