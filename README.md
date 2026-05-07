# Ad Creative Automation Toolkit — Simplified Version

> **Disclaimer**: To protect proprietary information, this project uses simulated data and sanitized API endpoints for demonstration purposes. All campaign names, spend figures, and credentials shown are synthetic. The code logic and architecture are fully preserved from the production version.

A full-pipeline automation system for mobile ad creative management — from asset upload and organization to performance monitoring and lifecycle cleanup. This page walks through **three representative modules** from the toolkit, each with a demo video and a code highlight, all on a single page — no need to click into any subfolder to read the full picture.

📄 [中文版](README_CN.md)

---

## Background

In mobile game advertising, operators must manage thousands of creative assets (videos, images, playable ads) across multiple ad platforms and 230+ countries. The daily routine involves:

- Manually uploading creatives to platform libraries
- Grouping them into Creative Sets by country, with strict naming rules and capacity limits (50 per set)
- Monitoring ROAS (Return on Ad Spend) across platforms to catch underperforming campaigns
- Periodically cleaning up low-spend creatives to optimize budget allocation

**These repetitive, error-prone tasks were ideal targets for automation.**

## What This Page Covers

The toolkit automates the entire creative lifecycle:

```
Upload --> Organize --> Maintain --> Monitor --> Cleanup
```

This page walks through three representative modules from the pipeline:

| Module | Problem Solved | Key Technique |
|--------|---------------|---------------|
| **D7 ROAS Predictor** | D7 profitability can't be measured until 7 days later | Logarithmic curve fitting + confidence scoring |
| **Spend-Based Cleanup** | No systematic way to remove underperformers; deletion risk | Priority chain + token-bucket rate limit + multi-layer safety |
| **Creative Set Manager** | API has no rename + 5+ ops scattered across tools | Delete-and-recreate safe rename + 3,800-line integrated GUI |

> The full toolkit also includes asset batch upload, Creative Set auto-population, playable ad maintenance, ROAS monitoring, and IPU trend visualization (4-5 supporting tools), kept in the private repo.

---

## Featured Projects

### 1. D7 ROAS Predictor — *Predictive Modeling + Data Visualization*

**The problem**: D7 ROAS (Return on Ad Spend at day 7) is the key profitability metric for mobile ad campaigns. But measuring it requires waiting a full 7 days after user acquisition — by then, an underperforming campaign may have already burned thousands of dollars with no way to recover.

**What I built**: A prediction model based on **logarithmic curve fitting** that forecasts D7 ROAS using only D0~D6 settled data, giving the team an actionable signal **3-5 days earlier** than traditional monitoring. The model runs inside a Streamlit dashboard alongside a real-time ROAS monitor covering Mintegral, Unity Ads, TikTok, and AppLovin.

**How the prediction works**:
- Model: `ROAS(t) = a * ln(t) + b` -- fits the diminishing-returns pattern of ad revenue over time
- Fitting: `scipy.optimize.curve_fit` on settled D0~D6 data points, extrapolates to t=8 (D7)
- Confidence scoring: >= 4 data points = high, 2-3 = medium, < 2 = insufficient
- R² goodness-of-fit reported for each prediction
- Plotly interactive charts: observed data points + fitted curve + predicted D7 + baseline + alert threshold on one plot

**Engineering highlights**:
- Settlement-aware data prep: a "day d" data point is only trustworthy after `stat_time >= (d+1)*24` hours
- Incremental Parquet caching: only re-pulls recent data that may still change, reducing DB load by ~50%
- Smart date windowing: auto-skips zero-revenue "bad data days" to prevent false alerts
- Defensive guards: pathological fits with `pred<=0` or `pred>10` are rejected
- Multi-day alert logic with configurable new-campaign protection period
- Country-level drilldown for each alerted campaign

📹 **Demo**: [`demo_d7_roas_predictor.mp4`](assets/demo_d7_roas_predictor.mp4) (click to download / play, ~9MB)

https://github.com/user-attachments/assets/1683a5a4-3f52-4020-8f32-15317a5b6f4c

**Code highlight** — the model and confidence-scored fit:

```python
def _log_model(x, a, b):
    return a * np.log(x) + b


def fit_and_predict(settled: dict) -> tuple[Optional[float], Optional[float], str]:
    """
    Input  settled: {day_index: roas_value}, where 0=D0 ... 7=D7
    x-axis: 1-indexed (D0 -> x=1, D7 -> x=8)
    Returns: (predicted_d7, r2, confidence)
    """
    days = sorted(k for k, v in settled.items() if v > 0)
    if len(days) < 2:
        return None, None, 'insufficient'

    x = np.array([d + 1 for d in days], dtype=float)
    y = np.array([settled[d] for d in days], dtype=float)

    try:
        popt, _ = curve_fit(_log_model, x, y, p0=[0.3, y[0]], maxfev=3000)
    except Exception:
        return None, None, 'insufficient'

    pred = _log_model(8.0, *popt)
    if pred <= 0 or pred > 10:           # sanity guard
        return None, None, 'insufficient'

    y_pred = _log_model(x, *popt)
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2 = float(1 - ss_res / ss_tot) if ss_tot > 0 else 0.0

    confidence = 'high' if len(days) >= 4 else 'medium'
    return float(pred), r2, confidence
```

→ Full snippet (with the settlement-aware data prep): [`highlights/01_roas_curve_fit.py`](highlights/01_roas_curve_fit.py)

---

### 2. Spend-Based Creative Cleanup — *Production-Grade Batch Operations*

**The problem**: Creative Sets accumulate low-performing assets over time, but cleaning them up manually is slow, and accidental deletions can hurt live campaigns. Without a systematic process, operators either never clean up (wasting spend) or manually review spreadsheets (slow and inconsistent).

**What I built**: A five-step pipeline (fetch -> query spend -> classify -> review -> execute) with **multiple safety layers**: mandatory Excel export before execution, double confirmation for destructive operations, JSON snapshots for rollback, and checkpoint/resume for interrupted runs.

**Workflow**:

```
Step 1: Fetch all Creative Sets from platform API (per selected Offer)
    |
Step 2: One-shot DB query for N-day spend data (creative_name -> spend)
    |
Step 3: Classify each video creative:
         whitelist (non-target language) | protected (< 14 days old)
         keep (spend above threshold)    | to_remove (spend in $0-$5 range)
    |
Step 3b: If ALL videos in a set are "to_remove" -> mark as "full_delete"
    |
Step 4: Export Excel for manual review (mandatory)
    |
Step 5: Execute with snapshot + checkpoint + concurrent API calls
```

**Key highlights**:
- Strict priority classification chain: `whitelist > protected > keep > to_remove > full_delete` -- each creative gets exactly one status, no ambiguity
- Spend lookup: name match first, falls back to MD5 match
- DB miss treated as $0 spend, NOT skipped — keeps forgotten creatives from leaking past the filter
- Multi-threaded execution with token-bucket rate limiting (30 req/min), shared across worker threads
- Checkpoint/resume: progress persisted after each API call, survives crashes
- Modular API client: session reuse + auto token refresh
- Hierarchical tree view with color-coded status for instant visual review

📹 **Demo**: [`demo_creative_cleanup.mp4`](assets/demo_creative_cleanup.mp4) (~35MB)

https://github.com/user-attachments/assets/6c3fcdf9-318e-43b7-9950-7892547a5f74

**Code highlight** — the priority chain:

```python
def classify_creative(creative_name, creative_md5, created_at, set_name,
                      name_to_spend, md5_to_spend, protect_cutoff_ts,
                      threshold_min, threshold_max, target_lang_codes):
    """
    Priority order (first match wins):
        whitelist  -> set is not in a target language (immune)
        protected  -> creative is too new (within protection window)
        keep       -> spend is OUTSIDE the to-remove range
        to_remove  -> spend is in the cleanup band [min, max)
    """
    spend, match = get_spend(creative_name, creative_md5, name_to_spend, md5_to_spend)
    if spend is None:
        spend = 0.0   # DB miss treated as $0, not skipped — must still classify

    if not _set_in_target_lang(set_name, target_lang_codes):
        return "whitelist", spend, match
    if (created_at or 0) > protect_cutoff_ts:
        return "protected", spend, match
    if not (threshold_min <= spend < threshold_max):
        return "keep", spend, match
    return "to_remove", spend, match
```

→ Full snippet (with the token-bucket rate limiter): [`highlights/02_cleanup_classifier.py`](highlights/02_cleanup_classifier.py)

---

### 3. Creative Set Manager — *All-in-One Campaign Management Tool*

**The problem**: Managing Creative Sets involves 5+ distinct operations (rename, batch create, material swap, GEO targeting fix, bulk delete) that operators did manually across different tools and platform pages. The worst part: the platform API doesn't even support direct renaming — when cloning a campaign to a new game, operators had to manually recreate every Creative Set with the new name (often 30+ Sets per offer).

**What I built**: A 3,800-line standalone GUI tool that consolidates all Creative Set operations into one interface. Browse Campaign -> Offer -> Creative Set hierarchy, then perform any operation with preview and full logging. The headline feature is **safe rename via delete-and-recreate**: capture full state, create new, delete old, with safety checks at every step.

**Key highlights**:
- Three-level hierarchical browsing: Campaign → Offer → Creative Set tree with status filtering
- Delete-and-recreate rename strategy: API doesn't support direct rename, so the tool captures full state (creatives, GEOs, ad_outputs), creates new, deletes old
- Pre-flight check: refuses if offer is at the 50-Set ceiling (the temporary +1 would bounce)
- **Order matters**: CREATE before DELETE, never the reverse
- HTML field preservation — early versions silently dropped `creative_type` and turned playable ads into broken video stubs
- **Asymmetric failure handling**: if create succeeds but delete fails, **never roll back** (the new Set is already serving live), log the orphan for manual cleanup
- Find-and-replace rename with diff preview before execution
- Smart batch creation from CSV config with customizable naming templates (`[Offer_name]_[Country]_video[SetNo]_[Date]`)
- Cross-set material consistency check + one-click replacement
- GEO targeting validation against `geos_mapping.json` + batch auto-fix
- Integrated API client with token caching (600s TTL), session reuse, and auto-pagination

📹 **Demo**: [`demo_creative_set_manager.mp4`](assets/demo_creative_set_manager.mp4) (~36MB)

https://github.com/user-attachments/assets/d4e901b8-a01f-4ddf-8513-71dc40fe166b

**Code highlight** — the asymmetric failure handling:

```python
# --- Step 3: Delete the old (ONLY after create succeeded) ---
delete_result = api.delete_creative_set(offer_id, old_name)
if not delete_result["success"]:
    # Asymmetric failure: new Set is already live and serving.
    # Do NOT roll back — that would cause a real outage.
    # Surface the orphan so the user can clean up manually.
    logger.warning(f"Old Set delete failed (orphan left): {delete_result.get('error')}")
    return {
        "success": False,
        "error": f"New Set created OK, but old Set delete failed: {delete_result.get('error')}",
        "api_response": {"create_success": True, "delete_success": False},
    }
```

→ Full snippet (with HTML field preservation + pre-flight check): [`highlights/03_set_rename_flow.py`](highlights/03_set_rename_flow.py)

---

## Tech Stack

- **Python** -- core language for all automation scripts
- **REST API Integration** -- Mintegral Open API (creative management, offer queries)
- **Database** -- MySQL (pymysql) for tracking uploads, spend data, and audit status
- **Data Analysis** -- pandas, numpy, scipy (curve fitting for ROAS prediction)
- **Visualization** -- Streamlit (interactive dashboards), Plotly (fitted curves), matplotlib (static charts)
- **GUI** -- tkinter for desktop tools with preview, pause/resume, and progress tracking
- **Concurrency** -- threading + token-bucket rate limiter (shared across worker threads)
- **Caching** -- Parquet-based incremental data caching

## Demo Mode & Mock Design

Since this project depends on live ad platform APIs and internal databases that are not publicly accessible, all featured scripts include a **built-in demo mode** that activates automatically when placeholder credentials are detected.

**How it works**: Each script checks whether the configured API key or DB host starts with `YOUR_`. If so, it switches to demo mode:

- **API calls** are replaced with mock responses that simulate successful operations (rename, delete, create, etc.)
- **Database queries** are replaced with pre-generated local data files (JSON / Parquet)
- **Real code paths are preserved** alongside the mock — the production logic is fully visible and intact, just gated behind an `if demo_mode` check

This is a standard engineering practice for portfolio projects that depend on proprietary infrastructure. The mock layer demonstrates the **workflow and architecture**, while the real code underneath demonstrates the **technical implementation**.

| Script | Demo Data | Mock Scope |
|--------|-----------|------------|
| D7 ROAS Predictor | `data_cache.parquet` (synthetic cohort data) | DB refresh mocked; analysis runs on real code |
| Creative Cleanup | `demo_scan_data.json` (860 creatives + spend) | API scan + DB query mocked; classification runs on real code |
| Creative Set Manager | `demo_data.json` (5 campaigns, 159 sets) | API CRUD mocked; GUI and business logic run on real code |

## Want the full source?

The complete sanitized source (full GUIs, supporting tools, database schemas, config files) is kept in a private repo. Happy to walk through it in detail.

---

> **Disclaimer**: This is a portfolio project. To protect proprietary information, all sensitive data (API keys, database credentials, internal paths, game titles) has been replaced with placeholders, and the project runs on simulated data with mock API responses. All campaign names, spend figures, and credentials shown are synthetic. The code logic and architecture are fully preserved from the production version.

📄 [中文版](README_CN.md)
