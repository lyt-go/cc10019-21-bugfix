"""LTV预测引擎：基于用户分群和历史行为预测生命周期价值。"""
from __future__ import annotations

from typing import Any

from app.store import store
from app.schemas import LtvResult
from app.services.retention_calc import get_retention_result


def predict_ltv(model_id: str) -> LtvResult:
    """执行LTV预测计算。"""
    model = store.find_by_field("lifetime_value", "model_id", model_id)
    if model is None:
        raise ValueError(f"LTV model not found: {model_id}")

    segment_id = model["segment_id"]
    value_metric = model["value_metric"]
    predict_window = model["predict_window"]
    decay_factor = model["decay_factor"]
    history_baseline = model["history_baseline"]

    # 获取分群信息
    segment = store.find_by_field("user_segment", "segment_id", segment_id)
    if segment is None:
        raise ValueError(f"Segment not found: {segment_id}")

    # 模拟历史基线数据
    base_size = segment.get("size_estimate", 1000)

    # 生成预测值
    predicted_values: list[dict[str, Any]] = []
    total_ltv = 0.0

    for user_idx in range(1, min(base_size + 1, 101)):  # 限制计算量
        user_id = f"U{user_idx:05d}"
        # 基于历史基线生成初始值
        if value_metric == "revenue":
            base_value = 50.0 + (user_idx % 20) * 10.0
        elif value_metric == "order_count":
            base_value = 1.0 + (user_idx % 10)
        else:  # active_days
            base_value = 3.0 + (user_idx % 15)

        # 应用衰减因子预测未来价值
        predicted = base_value * (decay_factor ** (predict_window / 30))
        predicted_values.append({
            "user_id": user_id,
            "base_value": round(base_value, 2),
            "predicted_value": round(predicted, 2),
            "predict_window_days": predict_window,
        })
        total_ltv += predicted

    avg_ltv = total_ltv / len(predicted_values) if predicted_values else 0.0

    # 模拟校准误差
    calibration_error = 0.05 + (1.0 - decay_factor) * 0.2

    result = LtvResult(
        model_id=model_id,
        segment_id=segment_id,
        predict_window=predict_window,
        predicted_values=predicted_values,
        avg_ltv=round(avg_ltv, 2),
        calibration_error=round(calibration_error, 4),
    )

    # 存储产物
    store.set_artifact("lifetime_value", model_id, result.model_dump())

    # 更新状态
    store.update("lifetime_value", model["id"], {
        "status": "已就绪",
        "pending": False,
    })

    return result


def get_ltv_result(model_id: str) -> dict[str, Any] | None:
    """获取LTV预测结果。"""
    artifact = store.get_artifact("lifetime_value", model_id)
    if artifact:
        return artifact
    try:
        result = predict_ltv(model_id)
        return result.model_dump()
    except ValueError:
        return None


def ltv_with_retention(model_id: str, retention_config_id: str) -> dict[str, Any]:
    """结合留存率校准LTV预测。

    委托价值校准服务：只使用留存计算已观察到的日期点；没有观察点或样本时
    不生成系数，原样返回原始预测并在 reason 里说明原因。
    """
    from app.services.value_calibration import calibrate_value

    calibration = calibrate_value(model_id, retention_config_id)
    observation = calibration.get("observation_source", {})
    calibrated = calibration.get("status") == "calibrated"
    return {
        "model_id": model_id,
        "retention_config_id": retention_config_id,
        "calibration_id": calibration.get("calibration_id", ""),
        "status": calibration.get("status"),
        "reason": calibration.get("reason", ""),
        "original_ltv": calibration.get("original_estimate", {}).get("avg_ltv", 0.0),
        "retention_adjusted_ltv": (
            calibration.get("calibrated_avg_ltv")
            if calibrated
            else calibration.get("original_estimate", {}).get("avg_ltv", 0.0)
        ),
        "calibration_coefficient": calibration.get("calibration_coefficient"),
        "avg_retention_rate": calibration.get("avg_retention_rate"),
        "valuation_type": calibration.get("valuation_type"),
        "observed_dates": observation.get("observed_dates", []),
        "valid_samples": observation.get("valid_samples", 0),
        "completion_version": observation.get("completion_version", ""),
        "confirmed_zero_revisit": calibration.get("confirmed_zero_revisit", False),
        "original_curve": calibration.get("original_curve", []),
    }
