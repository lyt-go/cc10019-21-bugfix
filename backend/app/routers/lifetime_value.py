"""生命周期价值路由：执行LTV预测、价值校准与跟进筛选。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.schemas import ActionPayload, ActionResult
from app.services.lifetime_value import predict_ltv, get_ltv_result
from app.services.value_calibration import (
    calibrate_value,
    create_followup_filter,
    followup_customers,
    get_calibration,
    get_followup_filter,
    list_calibrations,
    list_followup_filters,
    set_followup_state,
)
from app.store import store

router = APIRouter(prefix="/api/lifetime-value", tags=["lifetime_value"])


@router.get("")
def index(page: int = 1, size: int = 20) -> dict[str, Any]:
    rows = store.rows("lifetime_value")
    total = len(rows)
    start = (page - 1) * size
    return {"items": rows[start : start + size], "total": total, "page": page, "size": size}


@router.get("/export")
def export_entries() -> dict[str, Any]:
    rows = store.rows("lifetime_value")
    return {"items": rows, "total": len(rows)}


# ---- 价值校准（固定路径必须放在 /{entry_id} 之前） ----
@router.get("/summary")
def summary() -> dict[str, Any]:
    """看板 KPI：模型数、最新校准状态、可跟进与失败量。"""
    calibrations = list_calibrations()
    followups = list_followup_filters()
    latest_by_model: dict[str, dict[str, Any]] = {}
    for row in calibrations:
        latest_by_model[row["model_id"]] = row
    latest = list(latest_by_model.values())
    calibrated = sum(1 for row in latest if row.get("status") == "calibrated")
    failed = len(latest) - calibrated
    zero = sum(1 for row in latest if row.get("confirmed_zero_revisit"))
    latest_followup = followups[-1] if followups else None
    metrics = [
        {"label": "价值模型", "value": len(store.rows("lifetime_value")), "trend": 0},
        {"label": "已校准模型", "value": calibrated, "trend": 0},
        {"label": "校准失败", "value": failed, "trend": 0},
        {"label": "确认零回访", "value": zero, "trend": 0},
        {"label": "可跟进客户", "value": latest_followup.get("ready_count", 0) if latest_followup else 0, "trend": 0},
    ]
    return {"metrics": metrics, "latest_followup": latest_followup}


@router.get("/calibrations")
def calibrations(model_id: str | None = None, case: str | None = None) -> dict[str, Any]:
    rows = list_calibrations(model_id=model_id, case=case)
    return {"items": rows, "total": len(rows)}


@router.get("/calibrations/{calibration_id}")
def calibration_detail(calibration_id: str) -> dict[str, Any]:
    row = get_calibration(calibration_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Calibration not found")
    return row


@router.get("/followups")
def followups(model_id: str | None = None, case: str | None = None) -> dict[str, Any]:
    rows = list_followup_filters(model_id=model_id, case=case)
    return {"items": rows, "total": len(rows)}


@router.get("/followups/{filter_id}")
def followup_detail(filter_id: str) -> dict[str, Any]:
    row = get_followup_filter(filter_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Follow-up filter not found")
    return row


@router.post("/followups/{filter_id}/customers/{user_id}/state")
def update_followup_state(filter_id: str, user_id: str, payload: dict[str, Any]) -> ActionResult:
    """标记客户已安排跟进/暂不跟进（不改动已记录的估值）。"""
    try:
        row = set_followup_state(filter_id, user_id, str(payload.get("state", "")))
        return ActionResult(ok=True, message="Follow-up state updated", entry=row)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{entry_id}")
def detail(entry_id: int) -> dict[str, Any]:
    row = store.find("lifetime_value", entry_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    return row


@router.post("/{model_id}/run")
def run(model_id: str) -> ActionResult:
    try:
        result = predict_ltv(model_id)
        return ActionResult(ok=True, message="Predicted", entry=result.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{model_id}/result")
def result(model_id: str) -> dict[str, Any]:
    res = get_ltv_result(model_id)
    if res is None:
        raise HTTPException(status_code=404, detail="Result not found")
    return res


@router.post("/{model_id}/calibrate")
def calibrate(model_id: str, payload: dict[str, Any]) -> ActionResult:
    """生成价值校准数据。

    body: {retention_config_id, calibration_id?, reuse_current?}
    """
    retention_config_id = payload.get("retention_config_id", "")
    calibration_id = payload.get("calibration_id", "")
    try:
        if calibration_id:
            row = get_calibration(calibration_id)
            if row is None:
                raise ValueError(f"Calibration not found: {calibration_id}")
        else:
            if not retention_config_id:
                raise ValueError("retention_config_id is required")
            row = calibrate_value(
                model_id,
                retention_config_id,
                reuse_current=bool(payload.get("reuse_current", True)),
            )
        return ActionResult(ok=True, message=row.get("status", "calibrated"), entry=row)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{model_id}/calibration")
def latest_calibration(model_id: str, case: str | None = None, retention_config_id: str | None = None) -> dict[str, Any]:
    """取最新价值校准；带 retention_config_id 时会先按当前留存结果重算。"""
    try:
        if retention_config_id:
            row = calibrate_value(model_id, retention_config_id)
        else:
            rows = list_calibrations(model_id=model_id, case=case)
            row = rows[-1] if rows else None
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if row is None:
        raise HTTPException(status_code=404, detail="Calibration not found")
    return row


@router.post("/{model_id}/followup-filter")
def run_followup_filter(model_id: str, payload: dict[str, Any]) -> ActionResult:
    """按当前周期的价值结果筛选跟进客户并落一条筛选记录。

    body: {threshold, retention_config_id?, case?, calibration_id?}
    """
    try:
        threshold = float(payload.get("threshold", 0.0))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="threshold must be a number")
    try:
        row = create_followup_filter(
            model_id,
            threshold,
            retention_config_id=payload.get("retention_config_id") or None,
            case=payload.get("case") or None,
            calibration_id=payload.get("calibration_id") or None,
        )
        return ActionResult(ok=True, message=row["case"], entry=row)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{model_id}/followup")
def followup_list(model_id: str, threshold: float = 0.0, case: str | None = None, retention_config_id: str | None = None) -> dict[str, Any]:
    """直接查询某场景的跟进名单及采用的估值类型。"""
    try:
        return followup_customers(
            model_id,
            threshold,
            case=case,
            retention_config_id=retention_config_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{entry_id}/actions")
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    row = store.find("lifetime_value", entry_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    action = payload.action
    row["status"] = action
    row["pending"] = False
    return ActionResult(ok=True, message=action, entry=row)
