"""留存计算路由：执行留存计算并获取结果。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.schemas import ActionPayload, ActionResult
from app.services.retention_calc import (
    calculate_retention,
    confirm_zero_revisit,
    get_retention_result,
    set_observation,
)
from app.store import store

router = APIRouter(prefix="/api/retention-calc", tags=["retention_calc"])


@router.get("")
def index(page: int = 1, size: int = 20) -> dict[str, Any]:
    rows = store.rows("retention_calc")
    total = len(rows)
    start = (page - 1) * size
    # 附带最近一次计算口径，前端不用再逐个查结果。
    items = []
    for row in rows[start : start + size]:
        artifact = store.get_artifact("retention_calc", row["config_id"]) or {}
        merged = dict(row)
        for key in ("data_status", "completion_version", "observed_dates", "valid_samples"):
            if artifact.get(key) and key not in merged:
                merged[key] = artifact[key]
        items.append(merged)
    return {"items": items, "total": total, "page": page, "size": size}




@router.get("/summary")
def summary() -> dict[str, Any]:
    """看板 KPI：配置数、已完成、观察不足、确认零回访。"""
    rows = store.rows("retention_calc")
    metrics = [
        {"label": "留存配置", "value": len(rows), "trend": 0},
        {"label": "已完成", "value": sum(1 for r in rows if r.get("status") == "已完成"), "trend": 0},
        {"label": "暂无观察点", "value": sum(1 for r in rows if r.get("data_status") in {"no_observation", "no_sample", "correcting_enrollment"}), "trend": 0},
        {"label": "确认零回访", "value": sum(1 for r in rows if r.get("confirmed_zero_revisit")), "trend": 0},
    ]
    return {"metrics": metrics}


@router.get("/export")
def export_entries() -> dict[str, Any]:
    rows = store.rows("retention_calc")
    return {"items": rows, "total": len(rows)}
@router.get("/{entry_id}")
def detail(entry_id: int) -> dict[str, Any]:
    row = store.find("retention_calc", entry_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    return row


@router.post("/{config_id}/run")
def run(config_id: str) -> ActionResult:
    try:
        result = calculate_retention(config_id)
        return ActionResult(ok=True, message="Calculated", entry=result.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{config_id}/result")
def result(config_id: str) -> dict[str, Any]:
    res = get_retention_result(config_id)
    if res is None:
        raise HTTPException(status_code=404, detail="Result not found")
    return res


@router.post("/{config_id}/observation")
def update_observation(config_id: str, payload: dict[str, Any]) -> ActionResult:
    """维护观察口径：observation_state/observed_days/sample_size/confirmed_zero_revisit。"""
    try:
        row = set_observation(
            config_id,
            observation_state=payload.get("observation_state"),
            observed_days=payload.get("observed_days"),
            sample_size=payload.get("sample_size"),
            confirmed_zero_revisit=payload.get("confirmed_zero_revisit"),
        )
        return ActionResult(ok=True, message="Observation updated", entry=row)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{config_id}/confirm-zero-revisit")
def confirm_zero(config_id: str) -> ActionResult:
    """人工确认零回访后重算，零回访曲线仍作为有效观察参与价值校准。"""
    try:
        result = confirm_zero_revisit(config_id)
        return ActionResult(ok=True, message="Zero revisit confirmed", entry=result.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{entry_id}/actions")
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    row = store.find("retention_calc", entry_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    action = payload.action
    row["status"] = action
    row["pending"] = False
    return ActionResult(ok=True, message=action, entry=row)
