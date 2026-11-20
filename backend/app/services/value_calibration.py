"""价值校准：用留存计算的已观察曲线校准 LTV 原始预测。

设计要点：
- 只消费“已观察过”的留存日期点；空的留存曲线代表暂无观察点，不能被当成零回访。
- 确认零回访（confirmed_zero_revisit）是真实观察，仍然参与校准。
- 没有样本/没有回访观察点时只生成“校准失败”记录并说明原因，不生成系数；
  原始预测原样保留，供后续复算和旧报告核对。
- 每条校准记录都带观察来源（已观察日期、有效样本、完成版本）和原始预测快照，
  留存补算后重试不会覆盖旧报告。
"""
from __future__ import annotations

import datetime
from typing import Any

from app.schemas import FollowupFilterResult, ValueCalibrationResult
from app.store import store
from app.services.retention_calc import get_retention_result


CALIBRATION_MODULE = "value_calibration"
FOLLOWUP_MODULE = "followup_filter"
_FAILED_CASES = {"calibration_failed", "failed", "no_sample", "no_observation"}
_ZERO_CASES = {"confirmed_zero_revisit", "zero_revisit", "confirmed_zero"}
_READY = "可安排跟进"
_BLOCKED = "暂不跟进"


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _next_serial(module: str, field: str, prefix: str) -> str:
    """按现有记录生成下一个业务编号，例如 CAL-0001。"""
    max_no = 0
    for row in store.rows(module):
        raw = str(row.get(field, ""))
        tail = raw.rsplit("-", 1)[-1]
        if tail.isdigit():
            max_no = max(max_no, int(tail))
    return f"{prefix}-{max_no + 1:04d}"


def _next_report_version(module: str, model_id: str) -> int:
    """同一模型的第几次报告，旧报告版本不会被复用。"""
    versions = [
        int(row.get("report_version", 0) or 0)
        for row in store.rows(module)
        if row.get("model_id") == model_id
    ]
    return max(versions, default=0) + 1


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _main_curve(retention: dict[str, Any]) -> tuple[str, list[float]]:
    """取主队列（基准日）的留存曲线；没有基准日时退回第一条非空曲线。"""
    rates_by_key = retention.get("retention_rates", {}) or {}
    base_date = retention.get("base_date", "")
    if base_date in rates_by_key and rates_by_key[base_date]:
        return base_date, list(rates_by_key[base_date])
    for key, rates in rates_by_key.items():
        if rates:
            return key, list(rates)
    return base_date, []


def _observed_curve(retention: dict[str, Any]) -> list[dict[str, Any]]:
    """把留存产物整理成带日期的观察曲线。

    第 0 天是入组日基线（1.0），不是回访，因此不参与“回访留存”的均值计算，
    返回值里仍保留该点，方便前端和报告核对完整曲线。
    """
    key, rates = _main_curve(retention)
    observed_dates = list(retention.get("observed_dates", []) or [])
    base_date = retention.get("base_date", "")
    points: list[dict[str, Any]] = []
    for index, rate in enumerate(rates):
        date = observed_dates[index] if index < len(observed_dates) else ""
        if not date and base_date:
            date = _add_days(base_date, index)
        points.append({
            "cohort": key,
            "day": index,
            "date": date,
            "retention_rate": round(float(rate), 4),
            "is_enrollment_day": index == 0,
            "sample": int(
                (retention.get("sample_sizes", {}) or {}).get(key, 0)
                if rates
                else 0
            ),
        })
    return points


def _curve_average(points: list[dict[str, Any]]) -> float | None:
    """已观察曲线（含入组日）的平均留存率，与留存计算详情逐点对齐。"""
    rates = [float(point["retention_rate"]) for point in points]
    if not rates:
        return None
    return sum(rates) / len(rates)


def _revisit_average(points: list[dict[str, Any]]) -> float | None:
    """只看回访日（第 1 天起）的平均留存率，用于报告补充说明。"""
    rates = [
        float(point["retention_rate"])
        for point in points
        if not point.get("is_enrollment_day")
    ]
    if not rates:
        return None
    return sum(rates) / len(rates)


def _add_days(date_str: str, days: int) -> str:
    try:
        day = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    except (TypeError, ValueError):
        return ""
    return (day + datetime.timedelta(days=days)).strftime("%Y-%m-%d")


def _original_snapshot(model: dict[str, Any], ltv_result: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """原始预测快照：原始估值 + 原始曲线，写入校准记录后不再改动。"""
    curve = [
        {
            "user_id": item.get("user_id"),
            "base_value": item.get("base_value"),
            "predicted_value": item.get("predicted_value"),
            "predict_window_days": item.get("predict_window_days"),
        }
        for item in ltv_result.get("predicted_values", [])
    ]
    estimate = {
        "avg_ltv": ltv_result.get("avg_ltv", 0.0),
        "predict_window": ltv_result.get("predict_window", model.get("predict_window")),
        "value_metric": model.get("value_metric"),
        "model_version": model.get("version"),
        "predicted_user_count": len(curve),
        "calibration_error": ltv_result.get("calibration_error", 0.0),
    }
    return estimate, curve


def _empty_curve_reason(retention: dict[str, Any], points: list[dict[str, Any]]) -> str | None:
    """判断当前留存结果是否真的可以校准；不能校准时返回原因。"""
    data_status = retention.get("data_status", "observed")
    note = retention.get("observation_note", "")
    if data_status == "correcting_enrollment":
        return note or "指定留存组正在更正入组数据，暂时没有可用的观察点"
    if data_status == "no_observation":
        return note or "留存计算没有已观察到的日期，暂时没有可用的观察点"
    if data_status == "no_sample":
        return note or "留存计算没有有效样本，无法生成校准系数"
    if not retention.get("observed_dates"):
        return note or "留存结果没有已观察到的日期，无法生成校准系数"
    if not points:
        return note or "留存曲线没有已观察到的数据点，无法生成校准系数"
    if int(retention.get("valid_samples", 0) or 0) <= 0:
        return "留存结果有效样本为 0，无法生成校准系数"
    if _revisit_average(points) is None:
        return "仅观察到入组日、没有回访日数据，无法生成校准系数"
    return None


# ---------------------------------------------------------------------------
# 校准
# ---------------------------------------------------------------------------
def calibrate_value(
    model_id: str,
    retention_config_id: str,
    *,
    reuse_current: bool = True,
) -> dict[str, Any]:
    """对观察过的留存曲线计算校准系数，生成价值校准数据。"""
    # 延迟导入，避免与 lifetime_value 服务循环依赖。
    from app.services.lifetime_value import get_ltv_result, predict_ltv

    model = store.find_by_field("lifetime_value", "model_id", model_id)
    if model is None:
        raise ValueError(f"LTV model not found: {model_id}")

    ltv_result = get_ltv_result(model_id)
    if ltv_result is None:
        ltv_result = predict_ltv(model_id).model_dump()

    retention = get_retention_result(retention_config_id)
    if retention is None:
        raise ValueError(f"Retention result not found: {retention_config_id}")

    completion_version = retention.get("completion_version", "")
    existing = _latest_calibration(model_id)
    if (
        reuse_current
        and existing is not None
        and existing.get("retention_config_id") == retention_config_id
        and existing.get("observation_source", {}).get("completion_version") == completion_version
    ):
        # 同一份留存结果重复校准直接复用，避免无意义的报告版本膨胀。
        return existing

    points = _observed_curve(retention)
    estimate, curve = _original_snapshot(model, ltv_result)
    reason = _empty_curve_reason(retention, points)
    avg_retention: float | None = None
    coefficient: float | None = None
    calibrated_avg: float | None = None
    status = "calibration_failed"
    valuation_type = "original_prediction"

    revisit_avg: float | None = None
    if reason is None:
        # 均值按留存计算详情里的已观察曲线逐点计算（含入组日基线），
        # 保证两处口径一致；未观察到的尾部已被留存服务裁掉，不会以 0 参与。
        avg_retention = _curve_average(points)
        revisit_avg = _revisit_average(points)
        assert avg_retention is not None
        coefficient = round(0.5 + 0.5 * avg_retention, 6)
        calibrated_avg = round(float(estimate["avg_ltv"]) * coefficient, 2)
        status = "calibrated"
        valuation_type = "retention_calibrated"

    observation_source = {
        "retention_config_id": retention_config_id,
        "init_event": retention.get("init_event", ""),
        "return_event": retention.get("return_event", ""),
        "base_date": retention.get("base_date", ""),
        "observed_dates": list(retention.get("observed_dates", []) or []),
        "valid_samples": int(retention.get("valid_samples", 0) or 0),
        "sample_sizes": dict(retention.get("sample_sizes", {}) or {}),
        "completion_version": completion_version,
        "data_status": retention.get("data_status", "observed"),
        "confirmed_zero_revisit": bool(retention.get("confirmed_zero_revisit", False)),
        "observation_note": retention.get("observation_note", ""),
    }

    calibration_id = _next_serial(CALIBRATION_MODULE, "calibration_id", "CAL")
    result = ValueCalibrationResult(
        calibration_id=calibration_id,
        report_version=_next_report_version(CALIBRATION_MODULE, model_id),
        model_id=model_id,
        retention_config_id=retention_config_id,
        status=status,
        reason=reason or "",
        valuation_type=valuation_type,
        observation_source=observation_source,
        observed_retention_curve=points,
        original_curve=curve,
        avg_retention_rate=avg_retention,
        avg_revisit_rate=revisit_avg,
        calibration_coefficient=coefficient,
        original_estimate=estimate,
        calibrated_avg_ltv=calibrated_avg,
        confirmed_zero_revisit=bool(retention.get("confirmed_zero_revisit", False)),
        confirmed=status == "calibrated",
        created_at=_now(),
        observed_dates=observation_source["observed_dates"],
        valid_samples=observation_source["valid_samples"],
        completion_version=completion_version,
        data_status=observation_source["data_status"],
        coefficient=coefficient,
        original_avg_ltv=float(estimate["avg_ltv"]),
    )
    data = result.model_dump()

    # 行记录用于列表/筛选，产物用于按编号回看；两者内容一致且互不覆盖。
    store.insert(CALIBRATION_MODULE, dict(data))
    store.set_artifact(CALIBRATION_MODULE, calibration_id, data)
    return data


def _latest_calibration(model_id: str) -> dict[str, Any] | None:
    rows = [row for row in store.rows(CALIBRATION_MODULE) if row.get("model_id") == model_id]
    if not rows:
        return None
    return max(rows, key=lambda row: int(row.get("report_version", 0) or 0))


def get_calibration(calibration_id: str) -> dict[str, Any] | None:
    """按校准编号取回当时的报告（即使之后留存补算也保持原样）。"""
    artifact = store.get_artifact(CALIBRATION_MODULE, calibration_id)
    if artifact:
        return artifact
    return store.find_by_field(CALIBRATION_MODULE, "calibration_id", calibration_id)


def get_latest_calibration(
    model_id: str,
    *,
    case: str | None = None,
) -> dict[str, Any] | None:
    """按模型取最新校准，可限定确认零回访或校准失败场景。"""
    rows = [row for row in store.rows(CALIBRATION_MODULE) if row.get("model_id") == model_id]
    if case:
        rows = [row for row in rows if _match_case(row, case)]
    if not rows:
        return None
    return max(rows, key=lambda row: int(row.get("report_version", 0) or 0))


def _match_case(row: dict[str, Any], case: str) -> bool:
    normalized = (case or "").strip()
    if not normalized:
        return True
    # 筛选记录自带 case 字段，直接按记录场景匹配。
    if "case" in row:
        row_case = str(row.get("case", ""))
        if normalized in _ZERO_CASES:
            return row_case == "confirmed_zero_revisit"
        if normalized in _FAILED_CASES:
            return row_case == "calibration_failed"
        if normalized in {"retention_calibrated", "calibrated", "observed"}:
            return row_case == "retention_calibrated"
        return row_case == normalized
    if normalized in _ZERO_CASES:
        return bool(row.get("confirmed_zero_revisit"))
    if normalized in _FAILED_CASES:
        return row.get("status") == "calibration_failed"
    if normalized in {"retention_calibrated", "calibrated", "observed"}:
        return row.get("status") == "calibrated" and not row.get("confirmed_zero_revisit")
    return row.get("status") == normalized


def list_calibrations(
    model_id: str | None = None,
    case: str | None = None,
) -> list[dict[str, Any]]:
    """列出价值校准记录，支持按模型和场景过滤。"""
    rows = list(store.rows(CALIBRATION_MODULE))
    if model_id:
        rows = [row for row in rows if row.get("model_id") == model_id]
    if case:
        rows = [row for row in rows if _match_case(row, case)]
    return sorted(rows, key=lambda row: (row.get("model_id", ""), int(row.get("report_version", 0) or 0)))


# ---------------------------------------------------------------------------
# 跟进筛选
# ---------------------------------------------------------------------------
def _resolve_calibration(
    model_id: str,
    retention_config_id: str | None,
    case: str | None,
    calibration_id: str | None,
) -> dict[str, Any]:
    if calibration_id:
        calibration = get_calibration(calibration_id)
        if calibration is None:
            raise ValueError(f"Calibration not found: {calibration_id}")
        return calibration
    if retention_config_id:
        return calibrate_value(model_id, retention_config_id)
    calibration = get_latest_calibration(model_id, case=case)
    if calibration is not None:
        return calibration
    model = store.find_by_field("lifetime_value", "model_id", model_id)
    if model is None:
        raise ValueError(f"LTV model not found: {model_id}")
    fallback_config = str(model.get("retention_config_id", "") or "")
    if not fallback_config:
        raise ValueError("Retention calibration not found for model")
    calibration = calibrate_value(model_id, fallback_config)
    if case and not _match_case(calibration, case):
        # 兜底留存配置给不出所请求的场景时，宁可报错也不要返回一份
        # 场景不符的名单，避免运营按错场景安排跟进。
        raise ValueError(f"No {case} calibration for model {model_id}")
    return calibration


def create_followup_filter(
    model_id: str,
    threshold: float,
    *,
    retention_config_id: str | None = None,
    case: str | None = None,
    calibration_id: str | None = None,
) -> dict[str, Any]:
    """按当前周期价值结果筛选可跟进客户，并记录所用估值类型。

    校准失败时仍可用原始预测安排跟进：此时 valuation_type 为
    original_prediction，记录里保留原始曲线，方便复算和回看。
    """
    calibration = _resolve_calibration(model_id, retention_config_id, case, calibration_id)
    threshold = float(threshold)
    coefficient = calibration.get("calibration_coefficient")
    valuation_type = calibration.get("valuation_type", "original_prediction")
    calibrated = calibration.get("status") == "calibrated" and coefficient is not None

    items: list[dict[str, Any]] = []
    for point in calibration.get("original_curve", []):
        predicted = float(point.get("predicted_value", 0.0) or 0.0)
        valuation = round(predicted * float(coefficient), 2) if calibrated else round(predicted, 2)
        ready = valuation >= threshold
        reason = ""
        if not calibrated:
            reason = calibration.get("reason", "") or "留存观察不足，沿用原始预测"
        elif ready:
            reason = "价值达到本期跟进门槛"
        else:
            reason = "价值未达到本期跟进门槛"
        items.append({
            "user_id": point.get("user_id"),
            "predicted_value": round(predicted, 2),
            "valuation": valuation,
            "valuation_type": valuation_type,
            "ready": ready,
            "followup_ready": ready,
            "status": _READY if ready else _BLOCKED,
            "reason": reason,
        })

    ready_count = sum(1 for item in items if item["ready"])
    blocked_count = len(items) - ready_count
    filter_case = _case_of(calibration, case)
    filter_id = _next_serial(FOLLOWUP_MODULE, "filter_id", "FF")
    result = FollowupFilterResult(
        filter_id=filter_id,
        report_version=_next_report_version(FOLLOWUP_MODULE, model_id),
        model_id=model_id,
        threshold=threshold,
        case=filter_case,
        valuation_type=valuation_type,
        calibration_id=calibration.get("calibration_id", ""),
        observation_source=dict(calibration.get("observation_source", {})),
        original_estimate=dict(calibration.get("original_estimate", {})),
        original_curve=list(calibration.get("original_curve", [])),
        items=items,
        ready_count=ready_count,
        blocked_count=blocked_count,
        created_at=_now(),
        observed_dates=list(calibration.get("observation_source", {}).get("observed_dates", [])),
        valid_samples=int(calibration.get("observation_source", {}).get("valid_samples", 0) or 0),
        completion_version=calibration.get("observation_source", {}).get("completion_version", ""),
        original_avg_ltv=float(calibration.get("original_estimate", {}).get("avg_ltv", 0.0) or 0.0),
        coefficient=coefficient if calibrated else None,
        ready_customers=[item["user_id"] for item in items if item["ready"]],
        blocked_customers=[item["user_id"] for item in items if not item["ready"]],
    )
    data = result.model_dump()
    store.insert(FOLLOWUP_MODULE, dict(data))
    store.set_artifact(FOLLOWUP_MODULE, filter_id, data)
    return data


def set_followup_state(filter_id: str, user_id: str, state: str) -> dict[str, Any]:
    """在筛选记录上标记某个客户是否已安排跟进。

    只改筛选记录里该客户的安排状态，估值与原始曲线保持不动，
    回看旧报告时仍能核对原值和曲线。
    """
    row = get_followup_filter(filter_id)
    if row is None:
        raise ValueError(f"Follow-up filter not found: {filter_id}")
    normalized = (state or "").strip()
    if normalized not in {_READY, _BLOCKED}:
        raise ValueError(f"Unsupported follow-up state: {state}")
    target = None
    for item in row.get("items", []):
        if item.get("user_id") == user_id:
            target = item
            break
    if target is None:
        raise ValueError(f"Customer not in filter: {user_id}")
    target["followup_state"] = normalized
    target["scheduled"] = normalized == _READY
    target["status"] = _READY if target.get("ready") else _BLOCKED
    # 产物用于按编号回看，行记录用于列表/筛选，两处保持同一份安排状态。
    store.set_artifact(FOLLOWUP_MODULE, filter_id, row)
    for table_row in store.rows(FOLLOWUP_MODULE):
        if table_row.get("filter_id") == filter_id:
            table_row["items"] = row.get("items", [])
            break
    return row


def _case_of(calibration: dict[str, Any], requested: str | None) -> str:
    if calibration.get("confirmed_zero_revisit"):
        return "confirmed_zero_revisit"
    if calibration.get("status") == "calibration_failed":
        return "calibration_failed"
    return "retention_calibrated"


def get_followup_filter(filter_id: str) -> dict[str, Any] | None:
    artifact = store.get_artifact(FOLLOWUP_MODULE, filter_id)
    if artifact:
        return artifact
    return store.find_by_field(FOLLOWUP_MODULE, "filter_id", filter_id)


def list_followup_filters(
    model_id: str | None = None,
    case: str | None = None,
) -> list[dict[str, Any]]:
    rows = list(store.rows(FOLLOWUP_MODULE))
    if model_id:
        rows = [row for row in rows if row.get("model_id") == model_id]
    if case:
        rows = [row for row in rows if _match_case(row, case)]
    return sorted(rows, key=lambda row: (row.get("model_id", ""), int(row.get("report_version", 0) or 0)))


def followup_customers(
    model_id: str,
    threshold: float,
    *,
    case: str | None = None,
    retention_config_id: str | None = None,
) -> dict[str, Any]:
    """按场景直接查询跟进名单及采用的估值（不落新报告）。"""
    calibration = get_latest_calibration(model_id, case=case)
    if calibration is None and retention_config_id:
        fresh = calibrate_value(model_id, retention_config_id)
        calibration = fresh if (not case or _match_case(fresh, case)) else None
    if calibration is None:
        raise ValueError(
            f"Calibration not found for model {model_id}"
            + (f" in case {case}" if case else "")
        )
    coefficient = calibration.get("calibration_coefficient")
    calibrated = calibration.get("status") == "calibrated" and coefficient is not None
    items = []
    for point in calibration.get("original_curve", []):
        predicted = float(point.get("predicted_value", 0.0) or 0.0)
        valuation = round(predicted * float(coefficient), 2) if calibrated else round(predicted, 2)
        if valuation >= float(threshold):
            items.append({
                "user_id": point.get("user_id"),
                "predicted_value": round(predicted, 2),
                "valuation": valuation,
                "valuation_type": calibration.get("valuation_type", "original_prediction"),
            })
    return {
        "model_id": model_id,
        "case": _case_of(calibration, case),
        "calibration_id": calibration.get("calibration_id", ""),
        "valuation_type": calibration.get("valuation_type", "original_prediction"),
        "observation_source": calibration.get("observation_source", {}),
        "original_estimate": calibration.get("original_estimate", {}),
        "original_curve": calibration.get("original_curve", []),
        "items": items,
        "total": len(items),
    }
