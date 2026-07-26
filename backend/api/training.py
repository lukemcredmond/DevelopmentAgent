"""Offline training export endpoints (JSONL only — no in-app training)."""

from fastapi import APIRouter, Query

from backend.services.training_export import export_training_jsonl

router = APIRouter()


@router.get("/api/training/export")
def training_export(limit: int = Query(default=50, ge=1, le=500)):
    return export_training_jsonl(limit=limit)
