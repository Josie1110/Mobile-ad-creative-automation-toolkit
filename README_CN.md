# 广告素材自动化工具集 — 精简版

> **声明**: 为避免涉及公司敏感信息,本项目使用了模拟数据和脱敏后的接口配置进行展示。所有 campaign 名称、消耗数据和凭据均为虚构。代码逻辑和架构与生产版本完全一致。

一套覆盖移动广告素材全生命周期的自动化系统 -- 从素材上传、组织、监控到清理。本页选取其中**三个代表性模块**展开,每个都附 Demo 视频 + 核心代码片段,不需要点开任何子目录即可看完。

📄 [English](README.md)

---

## 背景

在移动游戏广告投放中,运营人员需要管理跨多个广告平台、覆盖 230+ 个国家的数千个素材文件(视频、图片、试玩广告)。日常工作包括:

- 手动将素材上传到平台素材库
- 按国家将素材分组到素材组(Creative Set),遵守严格的命名规则和容量限制(每组 50 个上限)
- 跨平台监控 ROAS(广告支出回报率),及时发现低效 campaign
- 定期清理低消耗素材以优化预算分配

**这些重复、易出错的工作是自动化的理想目标。**

## 项目功能(本页展示)

工具集覆盖素材全生命周期:

```
上传 --> 组织 --> 维护 --> 监控 --> 清理
```

本页选取其中三个代表性模块展开:

| 模块 | 解决的问题 | 核心技术 |
|------|-----------|---------|
| **D7 ROAS 预测** | D7 盈利指标要等 7 天才能看到 | 对数曲线拟合 + 置信度评分 |
| **基于消耗的素材清理** | 没有系统化方式移除低效素材,误删风险高 | 优先级判定链 + token-bucket 限流 + 多重安全层 |
| **素材组管理器** | API 不支持重命名 + 5+ 操作分散在不同工具 | "删-建"安全重命名 + 3,800 行集成 GUI |

> 完整工具集还包含素材批量上传、素材组自动创建、试玩素材维护、ROAS 监控、IPU 趋势可视化等 4-5 个配套工具,保留在私有仓库。

---

## 亮点项目

### 1. D7 ROAS 预测工具 -- *预测建模 + 数据可视化*

**痛点**: D7 ROAS(第 7 天广告支出回报率)是衡量 campaign 盈利能力的核心指标,但需要等待整整 7 天才能得到实际数据。在此期间,低效 campaign 可能已经浪费了数千美元。

**我的方案**: 基于**对数曲线拟合**的预测模型,利用 D0~D6 已结算数据预测 D7 ROAS,比传统监控**提前 3-5 天**给出预警信号。模型运行在 Streamlit 交互式仪表板上,支持 Mintegral、Unity Ads、TikTok 和 AppLovin 四个平台。

**预测原理**:
- 模型: `ROAS(t) = a * ln(t) + b` -- 拟合广告收入随时间递减增长的规律
- 拟合: 使用 `scipy.optimize.curve_fit` 对已结算数据点拟合,外推到 t=8(D7)
- 置信度: >= 4 个数据点为高置信度,2-3 个为中等,< 2 个为不足
- 每次预测都报告 R² 拟合优度
- Plotly 交互式图表: 数据点 + 拟合曲线 + 预测值 + 基准线 + 预警线同屏展示

**工程亮点**:
- "结算时效"感知的数据预处理: 第 d 天的数据需要 `stat_time >= (d+1)*24` 小时才算可信
- 增量 Parquet 缓存: 只重新拉取可能仍在变化的近期数据,减少约 50% 的数据库负载
- 智能日期窗口: 自动跳过零收入的"坏数据天",避免误报
- 防御性兜底: `pred<=0` 或 `pred>10` 的病态拟合直接拒绝
- 多天预警逻辑 + 新 campaign 保护期机制
- 按国家钻取每个预警 campaign 的消耗和 ROAS

📹 **Demo**: [`demo_d7_roas_predictor.mp4`](assets/demo_d7_roas_predictor.mp4)(点击下载播放,~9MB)

https://github.com/user-attachments/assets/1683a5a4-3f52-4020-8f32-15317a5b6f4c

**核心代码** -- 模型 + 带置信度评分的拟合:

```python
def _log_model(x, a, b):
    return a * np.log(x) + b


def fit_and_predict(settled: dict) -> tuple[Optional[float], Optional[float], str]:
    """
    输入 settled: {day_index: roas_value},0=D0 ... 7=D7
    x 轴 1-indexed (D0 -> x=1, D7 -> x=8)
    返回: (predicted_d7, r2, confidence)
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
    if pred <= 0 or pred > 10:           # 病态拟合兜底
        return None, None, 'insufficient'

    y_pred = _log_model(x, *popt)
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2 = float(1 - ss_res / ss_tot) if ss_tot > 0 else 0.0

    confidence = 'high' if len(days) >= 4 else 'medium'
    return float(pred), r2, confidence
```

→ 完整片段(含结算感知的数据预处理): [`highlights/01_roas_curve_fit.py`](highlights/01_roas_curve_fit.py)

---

### 2. 基于消耗的素材清理 -- *生产级批量操作*

**痛点**: 素材组中会逐渐积累低效素材,但手动清理既慢又有风险 -- 误删好素材会直接影响投放效果。没有系统化流程,运营要么从不清理(浪费消耗),要么手动看表(慢且不一致)。

**我的方案**: 五步流水线(拉取 -> 查消耗 -> 分类 -> 审核 -> 执行),配备**多重安全机制**:执行前必须导出 Excel 审核、破坏性操作需二次确认、JSON 快照支持回滚、断点续传支持中断恢复。

**工作流程**:

```
Step 1: 从平台 API 拉取所有 Creative Set(按选定 Offer)
    |
Step 2: 一次性 DB 查询 N 天消耗数据(creative_name -> spend)
    |
Step 3: 给每个视频素材分类:
         whitelist(非目标语言) | protected(< 14 天)
         keep(消耗超阈值)     | to_remove(消耗在 $0-$5 区间)
    |
Step 3b: 若一个 Set 内所有视频都是 to_remove -> 标记 full_delete
    |
Step 4: 导出 Excel 人工复核(强制)
    |
Step 5: 执行 + 快照 + 断点 + 并发 API 调用
```

**核心亮点**:
- 严格优先级判定链: `whitelist > protected > keep > to_remove > full_delete` -- 每个素材有且只有一个状态,无歧义
- 消耗查找: 先按 name 匹配,失败回退到 MD5 匹配
- DB 无记录按 $0 处理(而非跳过) -- 防止"被遗忘"的素材漏过过滤
- 多线程执行 + 令牌桶限速(30 次/分钟),跨工作线程共享
- 断点续传: 每次 API 调用后持久化进度,崩溃后可从断点恢复
- 模块化 API 客户端: Session 复用 + Token 自动刷新
- 树形视图 + 颜色编码,一眼看清每个素材的状态

📹 **Demo**: [`demo_creative_cleanup.mp4`](assets/demo_creative_cleanup.mp4)(~35MB)

https://github.com/user-attachments/assets/6c3fcdf9-318e-43b7-9950-7892547a5f74

**核心代码** -- 优先级判定链:

```python
def classify_creative(creative_name, creative_md5, created_at, set_name,
                      name_to_spend, md5_to_spend, protect_cutoff_ts,
                      threshold_min, threshold_max, target_lang_codes):
    """
    优先级(首匹配胜出):
        whitelist  -> Set 不在目标语言里(免疫清理)
        protected  -> 素材太新(在保护期内)
        keep       -> 消耗在删除带之外
        to_remove  -> 消耗落在清理带 [min, max)
    """
    spend, match = get_spend(creative_name, creative_md5, name_to_spend, md5_to_spend)
    if spend is None:
        spend = 0.0   # DB 查不到按 $0 处理,绝不跳过 -- 必须分类

    if not _set_in_target_lang(set_name, target_lang_codes):
        return "whitelist", spend, match
    if (created_at or 0) > protect_cutoff_ts:
        return "protected", spend, match
    if not (threshold_min <= spend < threshold_max):
        return "keep", spend, match
    return "to_remove", spend, match
```

→ 完整片段(含 token-bucket 限流器): [`highlights/02_cleanup_classifier.py`](highlights/02_cleanup_classifier.py)

---

### 3. 素材组管理器 -- *一体化 Campaign 管理工具*

**痛点**: 管理素材组涉及 5 种以上操作(重命名、批量创建、素材替换、地区修正、批量删除),运营人员需要在不同工具和平台页面之间切换。最头疼的是: 平台 API 不支持直接重命名 -- 克隆 campaign 到新游戏时,需要手动重建每个素材组,一个 offer 经常 30+ 个素材组。

**我的方案**: 3,800 行的单文件 GUI 工具,将所有素材组操作整合到一个界面。浏览 Campaign -> Offer -> Creative Set 三级层次,然后执行任意操作,全程有预览和日志。亮点功能是**通过"删-建"实现安全重命名**: 抓取完整状态 -> 新建 -> 删除旧的,每一步都有安全校验。

**核心亮点**:
- 三级层级浏览: Campaign -> Offer -> Creative Set 树,带状态筛选
- "删-建"重命名策略: API 不支持直接重命名,工具捕获完整状态(creatives、GEOs、ad_outputs)新建后再删除旧的
- 预检: offer 已到 50 个 Set 上限就拒绝(临时 +1 会被平台拒)
- **顺序至关重要**: 先建后删,绝不反过来
- HTML 字段保留 -- 早期版本曾因丢掉 `creative_type` 把 playable 广告变成损坏的视频残桩
- **非对称失败处理**: 若建成功但删失败,**绝不回滚**(新 Set 已经在跑了),只记录孤儿让用户手动处理
- 查找替换 + diff 预览,确认后才执行
- 从 CSV 配置批量创建,支持自定义命名模板(`[Offer_name]_[Country]_video[SetNo]_[Date]`)
- 素材一致性检查 + 一键替换
- 地区定向校验 + 批量自动修正
- 集成 API 客户端: Token 缓存(600s TTL) + Session 复用 + 自动分页

📹 **Demo**: [`demo_creative_set_manager.mp4`](assets/demo_creative_set_manager.mp4)(~36MB)

https://github.com/user-attachments/assets/d4e901b8-a01f-4ddf-8513-71dc40fe166b

**核心代码** -- 非对称失败处理:

```python
# --- 步骤 3: 删除旧 Set (仅在 create 成功后才执行) ---
delete_result = api.delete_creative_set(offer_id, old_name)
if not delete_result["success"]:
    # 非对称失败:新 Set 已经在跑了
    # 绝对不能回滚 —— 那会造成真实的投放中断
    # 把孤儿 Set 暴露出来,让用户手动清理
    logger.warning(f"旧 Set 删除失败(留下孤儿): {delete_result.get('error')}")
    return {
        "success": False,
        "error": f"新 Set 创建成功,但旧 Set 删除失败: {delete_result.get('error')}",
        "api_response": {"create_success": True, "delete_success": False},
    }
```

→ 完整片段(含 HTML 字段保留 + 预检): [`highlights/03_set_rename_flow.py`](highlights/03_set_rename_flow.py)

---

## 技术栈

- **Python** -- 所有脚本的核心语言
- **REST API** -- Mintegral Open API(素材管理、Offer 查询)
- **数据库** -- MySQL (pymysql),用于记录上传、消耗数据和审核状态
- **数据分析** -- pandas、numpy、scipy(ROAS 预测的曲线拟合)
- **可视化** -- Streamlit(交互仪表板)、Plotly(拟合曲线)、matplotlib(静态图表)
- **GUI** -- tkinter 桌面工具,支持预览、暂停/恢复、进度跟踪
- **并发** -- threading + token-bucket 限流(跨工作线程共享)
- **缓存** -- 基于 Parquet 的增量数据缓存

## Demo 模式与 Mock 设计

由于本项目依赖的广告平台 API 和内部数据库无法公开访问,所有亮点脚本都内置了 **Demo 模式**,检测到占位符凭据时自动激活。

**工作原理**: 每个脚本检查 API Key 或数据库 Host 是否以 `YOUR_` 开头,如果是则切换到 Demo 模式:

- **API 调用** 替换为模拟成功响应(重命名、删除、创建等操作返回 success)
- **数据库查询** 替换为本地预生成的数据文件(JSON / Parquet)
- **真实代码路径完整保留**,Mock 层通过 `if demo_mode` 条件判断与真实逻辑并存

这是求职项目中依赖私有基础设施时的**标准工程实践**。Mock 层展示的是**工作流程和架构设计**,而保留的真实代码展示的是**技术实现能力**。

| 脚本 | Demo 数据 | Mock 范围 |
|------|----------|----------|
| D7 ROAS 预测 | `data_cache.parquet`(合成 cohort 数据) | DB 刷新被 mock;分析逻辑跑真实代码 |
| 素材清理 | `demo_scan_data.json`(860 条素材+消耗) | API 扫描 + DB 查询被 mock;分类逻辑跑真实代码 |
| 素材组管理 | `demo_data.json`(5 个 campaign, 159 个素材组) | API 增删改被 mock;GUI 和业务逻辑跑真实代码 |

## 想看完整源码?

完整脱敏源码(全部 GUI、配套工具、数据库 schema、配置文件)保留在私有仓库,可详细介绍。

---

> **声明**: 这是一个求职展示项目。为避免涉及公司敏感信息,所有敏感数据(API 密钥、数据库凭据、内部路径、具体游戏名称)已替换为占位符,项目使用模拟数据和 Mock API 响应运行。所有 campaign 名称、消耗数据和凭据均为虚构。代码逻辑和架构与生产版本完全一致。

📄 [English](README.md)
