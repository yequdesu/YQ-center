"""Compatibility Admin router kept for legacy imports."""

from fastapi import APIRouter

router = APIRouter(prefix="/admin", tags=["admin"])
