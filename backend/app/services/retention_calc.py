"""留存计算引擎：计算用户留存率，支持按维度分段。"""
from __future__ import annotations

from typing import Any
from collections import defaultdict

from app.store import store
from app.schemas import RetentionResult


def _generate_retention_events(
    event_id: str,
    base_date: str,
    days: int,
    sample_size: int | None = None,
) -> list[dict[str, Any]]:
    """生成留存分析所需的模拟事件数据。

    sample_size 用于模拟“入库样本不足”的场景；为 0 时不产生任何可观察事件。
    """
    events = []
    import datetime
    base = datetime.datetime.strptime(base_date, "%Y-%m-%d")
    base_ts = int(base.timestamp())

    for day in range(days):
        # 每天的用户数递减模拟留存
        num_users = max(1000 - day * 80, 200)
        if sample_size is not None:
            num_users = min(num_users, max(int(sample_size), 0))
        for user_idx in range(1, num_users + 1):
            user_id = f"U{user_idx:05d}"
            # 用户在不同维度上的分布
            attr_001 = ["app", "web", "miniapp", "h5"][user_idx % 4]
            attr_005 = ["普通", "银卡", "金卡", "钻石"][user_idx % 4]
            events.append({
                "user_id": user_id,
                "event_id": event_id,
                "timestamp": base_ts + day * 86400 + (user_idx % 3600),
                "ATTR-001": attr_001,
                "ATTR-005": attr_005,
                "date": (base + datetime.timedelta(days=day)).strftime("%Y-%m-%d"),
            })
    return events


def calculate_retention(config_id: str) -> RetentionResult:
    """执行留存计算。"""
    config = store.find_by_field("retention_calc", "config_id", config_id)
    if config is None:
        raise ValueError(f"Retention config not found: {config_id}")

    init_event = config["init_event"]
    return_event = config["return_event"]
    calc_period = config.get("calc_period", "day")
    segment_dim = config.get("segment_dim", "")
    base_date = config["base_date"]
    retention_window = config.get("retention_window", 30)

    # 生成事件数据（可用 sample_size 模拟样本不足）
    sample_size = config.get("sample_size")
    if sample_size is not None:
        sample_size = int(sample_size)
    events = _generate_retention_events(init_event, base_date, retention_window, sample_size)

    # 按日期和用户分组
    date_users: dict[str, set[str]] = defaultdict(set)
    dim_date_users: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))

    for e in events:
        date_users[e["date"]].add(e["user_id"])
        if segment_dim:
            dim_val = e.get(segment_dim, "unknown")
            dim_date_users[dim_val][e["date"]].add(e["user_id"])

    # 计算留存率
    cohort_sizes: dict[str, int] = {}
    retention_rates: dict[str, list[float]] = {}

    # 基准日期的用户作为初始队列
    init_date = base_date
    init_users = date_users.get(init_date, set())
    cohort_sizes[init_date] = len(init_users)
    retention_rates[init_date] = []

    for day in range(retention_window):
        target_date = _add_days(base_date, day)
        target_users = date_users.get(target_date, set())
        retained = init_users & target_users
        rate = len(retained) / len(init_users) if init_users else 0.0
        retention_rates[init_date].append(round(rate, 4))

    # 按维度分段
    if segment_dim:
        for dim_val, dim_data in dim_date_users.items():
            dim_init_users = dim_data.get(init_date, set())
            key = f"{segment_dim}:{dim_val}"
            cohort_sizes[key] = len(dim_init_users)
            retention_rates[key] = []
            for day in range(retention_window):
                target_date = _add_days(base_date, day)
                target_users = dim_data.get(target_date, set())
                retained = dim_init_users & target_users
                rate = len(retained) / len(dim_init_users) if dim_init_users else 0.0
                retention_rates[key].append(round(rate, 4))

    # ---- 观察口径：区分“已观察”“零回访（已确认）”“暂无可观察点” ----
    observation_state = str(config.get("observation_state", "observed") or "observed")
    confirmed_zero = bool(config.get("confirmed_zero_revisit", False))
    observed_days = config.get("observed_days")
    if observed_days is None:
        # 默认按今天回看：基准日之后还没发生的日期不算观察点，
        # 更不能把尚未到达的日期当成零回访。
        import datetime
        today = datetime.date.today()
        try:
            base = datetime.date.fromisoformat(base_date)
            observed_days = (today - base).days + 1
        except (TypeError, ValueError):
            observed_days = retention_window
    observed_days = max(0, min(int(observed_days), retention_window))

    data_status = "observed"
    observation_note = ""
    observed_dates: list[str] = []
    sample_sizes: dict[str, int] = {}

    if observation_state == "correcting_enrollment":
        # 指定的留存组还在更正入组数据，这一轮没有可用观察点。
        data_status = "correcting_enrollment"
        observation_note = "指定留存组正在更正入组数据，暂时没有可用的观察点"
        retention_rates = {key: [] for key in retention_rates}
        cohort_sizes = {key: 0 for key in cohort_sizes}
        observed_days = 0
    elif observed_days == 0:
        data_status = "no_observation"
        observation_note = "留存计算没有已观察到的日期，暂时没有可用的观察点"
        retention_rates = {key: [] for key in retention_rates}
    else:
        observed_dates = [_add_days(base_date, day) for day in range(observed_days)]
        # 只保留实际观察到的日期点，未观察到的尾部不能当成零留存。
        retention_rates = {
            key: rates[:observed_days] for key, rates in retention_rates.items()
        }
        if confirmed_zero:
            data_status = "confirmed_zero_revisit"
            observation_note = "已确认该留存组零回访，零值来自真实观察"
            # 第 0 天是入组日基线（1.0），之后各天回访为 0。
            retention_rates = {
                key: ([1.0] if rates else []) + [0.0] * max(len(rates) - 1, 0)
                for key, rates in retention_rates.items()
            }

    for key, rates in retention_rates.items():
        sample_sizes[key] = cohort_sizes.get(key, 0) if rates else 0
    main_key = init_date if init_date in sample_sizes else next(iter(sample_sizes), "")
    valid_samples = sample_sizes.get(main_key, 0)

    if data_status == "observed" and valid_samples <= 0:
        # 有日期但没有入库样本：同样不是“零回访”，只是暂时无法校准。
        data_status = "no_sample"
        observation_note = "留存计算没有有效样本，暂时没有可用的观察点"
        retention_rates = {key: [] for key in retention_rates}
        observed_dates = []

    # 每次完成计算都推进完成版本，供价值校准记录引用。
    completion_seq = int(config.get("completion_seq", 0) or 0) + 1
    completion_version = f"{config_id}-v{completion_seq}"

    result = RetentionResult(
        config_id=config_id,
        init_event=init_event,
        return_event=return_event,
        base_date=base_date,
        cohort_sizes=cohort_sizes,
        retention_rates=retention_rates,
        result_table=config.get("result_table", ""),
        observed_dates=observed_dates,
        valid_samples=valid_samples,
        sample_sizes=sample_sizes,
        completion_version=completion_version,
        data_status=data_status,
        observation_note=observation_note,
        confirmed_zero_revisit=confirmed_zero and data_status == "confirmed_zero_revisit",
    )

    # 存储产物
    store.set_artifact("retention_calc", config_id, result.model_dump())

    # 更新状态
    store.update("retention_calc", config["id"], {
        "status": "已完成",
        "pending": False,
        "completion_seq": completion_seq,
        "completion_version": completion_version,
        "data_status": data_status,
    })

    return result


def set_observation(
    config_id: str,
    *,
    observation_state: str | None = None,
    observed_days: int | None = None,
    sample_size: int | None = None,
    confirmed_zero_revisit: bool | None = None,
) -> dict[str, Any]:
    """维护留存计算的观察口径（已观察天数/样本/是否确认零回访）。"""
    config = store.find_by_field("retention_calc", "config_id", config_id)
    if config is None:
        raise ValueError(f"Retention config not found: {config_id}")
    updates: dict[str, Any] = {}
    if observation_state is not None:
        updates["observation_state"] = observation_state
    if observed_days is not None:
        updates["observed_days"] = max(0, int(observed_days))
    if sample_size is not None:
        updates["sample_size"] = max(0, int(sample_size))
    if confirmed_zero_revisit is not None:
        updates["confirmed_zero_revisit"] = bool(confirmed_zero_revisit)
    row = store.update("retention_calc", config["id"], updates) or config
    if updates:
        # 观察口径变了，旧的留存产物不能再被价值校准消费。
        store.clear_artifact("retention_calc", config_id)
    return row


def confirm_zero_revisit(config_id: str) -> "RetentionResult":
    """人工确认零回访：之后重算的留存曲线会被标记为已确认零回访观察。"""
    set_observation(config_id, observation_state="observed", confirmed_zero_revisit=True)
    return calculate_retention(config_id)


def _add_days(date_str: str, days: int) -> str:
    """日期加减。"""
    import datetime
    d = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    return (d + datetime.timedelta(days=days)).strftime("%Y-%m-%d")


def get_retention_result(config_id: str) -> dict[str, Any] | None:
    """获取留存计算结果。"""
    artifact = store.get_artifact("retention_calc", config_id)
    if artifact:
        return artifact
    try:
        result = calculate_retention(config_id)
        return result.model_dump()
    except ValueError:
        return None
