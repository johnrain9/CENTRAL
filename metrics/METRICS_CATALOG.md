# CENTRAL Metrics Catalog

Comprehensive catalog of metrics derivable from the CENTRAL task system, dispatcher, and worker reports. Organized by theme with data source annotations.

---

## 1. Model Performance & Comparison

| Metric | Description | Source |
|--------|-------------|--------|
| Success rate by model | `COMPLETED` vs `REWORK_REQUIRED` vs `FAILED` per `effective_worker_model` | task_runtime_state + worker results |
| First-pass success rate | Success with `retry_count = 0` | task_runtime_state |
| Rework cycle depth | Avg `rework_count` per model | tasks.metadata_json |
| Task duration by model | `finished_at - started_at` P50/P90/P99 distributions | task_runtime_state |
| Effort × model interaction | Cross-tab `worker_effort × effective_worker_model` vs success rate | task_execution_settings + task_runtime_state |
| Model × task_type | Which model excels at design vs impl vs audit? | tasks + task_runtime_state |
| Model fallback frequency | How often does a task override the dispatcher default? | task_runtime_state.worker_model_source |
| Model source breakdown | `task_override` vs `policy_default` vs `dispatcher_default` distribution | task_runtime_state.worker_model_source |

## 2. Task Success Rates

| Metric | Description | Source |
|--------|-------------|--------|
| By task_type | `implementation` vs `design` vs `infrastructure` pass rates | tasks + task_runtime_state |
| By initiative | Which epics have the most churn/rework? | tasks.initiative + task_runtime_state |
| By repo | Which repos get the cleanest first-pass work? | tasks.target_repo_id + task_runtime_state |
| By priority band | Does higher priority correlate with success? | tasks.priority + task_runtime_state |
| Validation pass rate | Individual validation check pass rates from worker output `validation[]` | worker result JSON |
| System fit verdict distribution | acceptable/marginal/risky per model/initiative | worker result JSON |

## 3. Throughput & Pipeline

| Metric | Description | Source |
|--------|-------------|--------|
| Queue depth over time | Tasks in `queued` state at any point | task_events / heartbeat |
| Daily/weekly throughput | Tasks completed per period, by repo and initiative | task_runtime_state.finished_at |
| Cycle time | `created_at → closed_at` (end-to-end) | tasks |
| Lead time | `created_at → claimed_at` (queue wait) | tasks + task_runtime_state |
| Work time | `started_at → finished_at` (active execution) | task_runtime_state |
| Review latency | `pending_review_at → done/failed` | task_runtime_state |
| State transition dwell times | Time spent in each state | task_events / task_runtime_state |
| Concurrency utilization | Avg active workers / max_workers over time | dispatcher heartbeat logs |

## 4. Retry & Failure Patterns

| Metric | Description | Source |
|--------|-------------|--------|
| Retry distribution | Histogram of `retry_count` at terminal state | task_runtime_state |
| Failure mode taxonomy | Clustered `last_runtime_error` strings | task_runtime_state |
| Max retries exhaustion rate | Tasks that hit the retry ceiling | task_runtime_state |
| Operator kill rate | Exit -15 events | task_runtime_state + dispatcher log |
| Timeout rate | `timeout` terminal states, by model and timeout_seconds | task_runtime_state + task_execution_settings |
| Retry recovery rate | Of tasks that retried, what % eventually succeeded? | task_runtime_state |

## 5. Worker Output Quality

| Metric | Description | Source |
|--------|-------------|--------|
| Completion ratio | `completed_items` / (`completed_items` + `remaining_items`) | worker result JSON |
| Blocker frequency | Recurring blockers across tasks | worker result JSON |
| Discovery density | Avg discoveries per task, by model and task_type | worker result JSON |
| Warning frequency | By task type and model (leading quality indicator) | worker result JSON |
| Files changed per task | Volume of work per run | worker result JSON |
| Artifact production rate | Tasks producing artifacts vs. not | worker result JSON / task_artifacts |
| Requirements coverage | Requirements assessed vs. met from `requirements_assessment[]` | worker result JSON |

## 6. Cost Proxy & Effort Calibration

| Metric | Description | Source |
|--------|-------------|--------|
| Effort level distribution | % of tasks at low/medium/high/max | task_execution_settings |
| Effort × duration | Does higher effort take longer? | task_execution_settings + task_runtime_state |
| Backend cost allocation | Estimated cost-hours by backend × effort × duration | task_runtime_state + task_execution_settings |
| High-effort ROI | Do high-effort tasks have lower rework counts? | task_execution_settings + tasks.metadata_json |
| Cost per success | Duration × effort proxy per successful task, by model | composite |

## 7. Audit & Review

| Metric | Description | Source |
|--------|-------------|--------|
| Audit agreement rate | Audit verdict vs impl task result | worker result JSON (audit tasks) |
| Audit-triggered rework rate | Impl COMPLETED → audit flagged REWORK_REQUIRED | task_runtime_state + worker results |
| Review aging | Time from pending_review_at to resolution | task_runtime_state |
| Review approval rate | First-review approval vs rejection | task_events |

## 8. Dependency & Scheduling

| Metric | Description | Source |
|--------|-------------|--------|
| Critical path length | Longest dependency chain to terminal task | task_dependencies |
| Blocking factor | Tasks with the most dependents | task_dependencies |
| Dependency wait time | `claimed_at` - max(`finished_at` of deps) | task_dependencies + task_runtime_state |
| Repo concurrency utilization | Active workers / max_concurrent_workers per repo | dispatcher heartbeat + repo metadata |

---

## Data Gaps (Need Small Additions)

### Token/Cost Tracking
- **Not currently stored.** Add `tokens_used`, `tokens_cost_usd` to worker result schema.
- Enables: true cost-per-task, cost-per-success, model cost comparison.

### Heartbeat History Table
- **Currently log-only.** Write dispatcher heartbeat snapshots to a DB table.
- Enables: time-series queue depth, concurrency utilization charts without log parsing.

### Worker Timing Breakdown
- **Not currently stored.** Add `time_to_first_output`, `thinking_time` to worker results.
- Enables: diagnosing where execution time goes.

### Exit Code Tracking
- **Partially available.** Normalize exit codes into the runtime state table.
- Enables: clean separation of quota errors vs timeouts vs code errors vs operator kills.

---

## Proposed Dashboard Views

| View | What It Answers |
|------|-----------------|
| **Model Scorecard** | Per model: success %, avg duration, rework rate, cost proxy |
| **Initiative Health** | Per epic: done/total, rework rate, blocked count |
| **Daily Throughput** | Tasks completed per day, stacked by repo |
| **Failure Taxonomy** | Top-N error strings, trends over time |
| **Effort Calibration** | Effort level vs. success rate cross-tab |
| **Queue Depth Timeline** | Task backlog over time (growing or shrinking?) |
| **Retry Heatmap** | Model × task_type retry frequency |
| **Worker Richness** | Discovery + decision density per task, by model |
| **Audit Feedback Loop** | How often auditors disagree with workers, by model |
| **Cost Efficiency** | Cost proxy per successful task, by model × effort |
