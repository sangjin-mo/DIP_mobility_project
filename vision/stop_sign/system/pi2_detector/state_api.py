# PC 제어 서버가 차체 출발/정지 시 호출하는 엔드포인트 (design/README.md §3-2)
# detector.py가 이 모듈의 vehicle_state를 공유해서 "지금 추론을 돌려야 하는지"를 판단한다.

import threading

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()


class VehicleState:
    def __init__(self):
        self._lock = threading.Lock()
        self._driving = False

    def set_driving(self, driving: bool) -> None:
        with self._lock:
            self._driving = driving

    def is_driving(self) -> bool:
        with self._lock:
            return self._driving


vehicle_state = VehicleState()


class VehicleStateRequest(BaseModel):
    driving: bool


@app.post("/vehicle-state")
def update_vehicle_state(body: VehicleStateRequest):
    vehicle_state.set_driving(body.driving)
    return {"driving": vehicle_state.is_driving()}
