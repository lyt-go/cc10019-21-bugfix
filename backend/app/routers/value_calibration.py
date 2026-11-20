"""价值校准路由别名：与生命周期价值校准入口共用同一套服务。

部分调用方习惯把校准数据当成独立资源访问，这里提供 /api/value-calibration/*
的等价入口，避免必须记住它挂在 LTV 模型下面。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.schemas import ActionResult
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

router = APIRouter(prefix="/api/value-calibration", tags=["value_calibration"])


@router.get("")
def index(model_id: str | None = None, case: str | None = None) -> dict[str, Any]:
    rows = list_calibrations(model_id=model_id, case=case)
    return {"items": rows, "total": len(rows)}


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
    try:
        row = set_followup_state(filter_id, user_id, str(payload.get("state", "")))
        return ActionResult(ok=True, message="Follow-up state updated", entry=row)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/followup")
def followup_query(
    model_id: str,
    threshold: float = 0.0,
    case: str | None = None,
    retention_config_id: str | None = None,
) -> dict[str, Any]:
    try:
        return followup_customers(
            model_id,
            threshold,
            case=case,
            retention_config_id=retention_config_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{model_id}/calibrate")
def calibrate(model_id: str, payload: dict[str, Any]) -> ActionResult:
    retention_config_id = payload.get("retention_config_id", "")
    if not retention_config_id:
        raise HTTPException(status_code=400, detail="retention_config_id is required")
    try:
        row = calibrate_value(
            model_id,
            retention_config_id,
            reuse_current=bool(payload.get("reuse_current", True)),
        )
        return ActionResult(ok=True, message=row.get("status", "calibrated"), entry=row)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{model_id}/followup-filter")
def run_followup_filter(model_id: str, payload: dict[str, Any]) -> ActionResult:
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


@router.get("/{calibration_id}")
def detail(calibration_id: str) -> dict[str, Any]:
    row = get_calibration(calibration_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Calibration not found")
    return row
