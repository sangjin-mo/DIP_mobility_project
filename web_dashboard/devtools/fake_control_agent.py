"""A no-motor FastAPI agent for checking dashboard control integration.

Run with:
    uvicorn web_dashboard.devtools.fake_control_agent:app --host 127.0.0.1 --port 9200
"""

from __future__ import annotations

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field


class ControlCommand(BaseModel):
    command_id: str
    command: Literal["START", "STOP", "HEARTBEAT"]
    sent_at_ms: int = Field(ge=0)
    target_speed_mps: float | None = Field(default=None, gt=0, le=1.0)


app = FastAPI(title="Fake PiRacer Control Agent")
_state = {"state": "STOPPED", "target_speed_mps": 0.0}


@app.get("/api/status")
async def status() -> dict:
    return dict(_state)


@app.post("/api/control")
async def control(command: ControlCommand) -> dict:
    if command.command == "START":
        if command.target_speed_mps is None:
            return {"accepted": False, "reason": "target_speed_mps is required"}
        _state.update(state="RUNNING", target_speed_mps=command.target_speed_mps)
    elif command.command == "STOP":
        _state.update(state="STOPPED", target_speed_mps=0.0)

    return {
        "accepted": True,
        "command_id": command.command_id,
        **_state,
    }
