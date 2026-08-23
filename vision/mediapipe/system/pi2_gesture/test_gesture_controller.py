# gesture_controller.py 순수 로직 단위 테스트 — 카메라/실제 손/네트워크 없이 실행 가능.
# dashboard_client는 Mock으로 대체 (design/README.md 검증 계획 §1).
#
# 2026-08-22: 정지/재출발도 dashboard_client(PC 경유)로 통합돼(§3-1-1),
# vehicle_client는 더 이상 없음 — GestureController가 클라이언트 하나만 받음.
#
# 실행: python -m unittest test_gesture_controller.py -v  (이 폴더에서)

import unittest
from unittest.mock import MagicMock, patch

import gesture_config as cfg
from gesture_controller import GestureController

FIST = cfg.GESTURE_STOP
UP = cfg.GESTURE_ACCELERATE
DOWN = cfg.GESTURE_DECELERATE


def make_controller():
    dashboard_client = MagicMock()
    dashboard_client.stop.return_value = {"accepted": True, "rover": {"state": "STOPPED", "target_speed_mps": 0.0}}
    dashboard_client.get_status.return_value = {"state": "RUNNING", "target_speed_mps": 0.25}
    dashboard_client.set_speed.return_value = {
        "accepted": True,
        "rover": {"state": "RUNNING", "target_speed_mps": 0.30},
    }

    controller = GestureController(dashboard_client)
    return controller, dashboard_client


def feed(controller: GestureController, label: str | None, times: int) -> None:
    for _ in range(times):
        controller.on_frame((label, 0.9) if label is not None else None)


class FistStopTests(unittest.TestCase):
    def test_requires_debounce_before_stopping(self):
        controller, dashboard_client = make_controller()
        feed(controller, FIST, cfg.GESTURE_DEBOUNCE_N - 1)
        dashboard_client.stop.assert_not_called()
        self.assertFalse(controller.is_stopped)

    def test_stop_fires_once_debounce_satisfied(self):
        controller, dashboard_client = make_controller()
        feed(controller, FIST, cfg.GESTURE_DEBOUNCE_N)
        dashboard_client.stop.assert_called_once()
        self.assertTrue(controller.is_stopped)

    def test_holding_fist_does_not_resend_stop(self):
        controller, dashboard_client = make_controller()
        feed(controller, FIST, cfg.GESTURE_DEBOUNCE_N + 20)
        dashboard_client.stop.assert_called_once()

    def test_releasing_fist_does_not_auto_resume(self):
        # 주먹은 "정지 버튼"일 뿐 — 손을 풀어도 자동 재출발(START)은 절대 보내지 않음
        # (요청 범위 밖 기능이라 명시적으로 제거함). 재출발은 사람이 대시보드에서 직접 함.
        controller, dashboard_client = make_controller()

        feed(controller, FIST, cfg.GESTURE_DEBOUNCE_N)
        self.assertTrue(controller.is_stopped)

        feed(controller, None, cfg.GESTURE_DEBOUNCE_M)
        dashboard_client.set_speed.assert_not_called()
        # 내부적으로는 재무장(다음 주먹을 다시 인식하기 위해)되어 is_stopped는 풀리지만,
        # 실제 차량에는 STOP만 나갔고 START는 절대 나가지 않았어야 함
        self.assertFalse(controller.is_stopped)

    def test_fist_can_be_triggered_again_after_release(self):
        controller, dashboard_client = make_controller()

        feed(controller, FIST, cfg.GESTURE_DEBOUNCE_N)
        feed(controller, None, cfg.GESTURE_DEBOUNCE_M)
        feed(controller, FIST, cfg.GESTURE_DEBOUNCE_N)

        self.assertEqual(dashboard_client.stop.call_count, 2)


class StopSignSharedLatchTests(unittest.TestCase):
    def test_stop_sign_still_auto_resumes_when_only_reason(self):
        # 표지판이 사라지면 자동 재출발하는 건 stop_sign 원래 기능이라 그대로 유지됨
        controller, dashboard_client = make_controller()

        controller.request_stop("STOP_SIGN")
        dashboard_client.stop.assert_called_once()

        controller.release_stop("STOP_SIGN")
        dashboard_client.set_speed.assert_called_once()
        self.assertFalse(controller.is_stopped)

    def test_fist_never_auto_resumes_even_as_sole_reason(self):
        controller, dashboard_client = make_controller()

        controller.request_stop("STOP_SIGN")
        feed(controller, FIST, cfg.GESTURE_DEBOUNCE_N)  # 표지판이 잡혀있는 동안 주먹도 겹침
        dashboard_client.stop.assert_called_once()  # 이미 정지 상태라 재전송 안 됨
        self.assertTrue(controller.is_stopped)

        controller.release_stop("STOP_SIGN")  # 표지판만 사라짐 (STOP_SIGN은 release_stop 경로라 자동재출발 대상이나, FIST가 남아있어 억제됨)
        dashboard_client.set_speed.assert_not_called()  # 주먹이 아직 남아있어 재출발 안 함
        self.assertTrue(controller.is_stopped)

        feed(controller, None, cfg.GESTURE_DEBOUNCE_M)  # 주먹도 풀림 (자동 재출발 없이 내부 사유만 비워짐)
        dashboard_client.set_speed.assert_not_called()
        self.assertFalse(controller.is_stopped)


class SpeedStepTests(unittest.TestCase):
    def test_accelerate_steps_and_rounds(self):
        controller, dashboard_client = make_controller()
        feed(controller, UP, cfg.GESTURE_DEBOUNCE_N)
        dashboard_client.set_speed.assert_called_once_with(cfg.DASHBOARD_URL, 0.30)

    def test_cooldown_suppresses_repeat_within_window(self):
        controller, dashboard_client = make_controller()
        with patch("gesture_controller.time.monotonic", side_effect=[0.0] * 200):
            feed(controller, UP, cfg.GESTURE_DEBOUNCE_N)
            feed(controller, UP, cfg.GESTURE_DEBOUNCE_N)  # 쿨타임(2초) 안 지남
        dashboard_client.set_speed.assert_called_once()

    def test_decelerate_floors_at_min_speed_without_stopping(self):
        controller, dashboard_client = make_controller()
        dashboard_client.get_status.return_value = {"state": "RUNNING", "target_speed_mps": cfg.MIN_SPEED_MPS}
        controller._sync_known_speed()

        feed(controller, DOWN, cfg.GESTURE_DEBOUNCE_N)

        dashboard_client.set_speed.assert_not_called()  # 하한에서는 클램프만, 요청 자체를 안 보냄
        dashboard_client.stop.assert_not_called()  # 정지로 전환하지 않음 (버그 수정 사항)
        self.assertFalse(controller.is_stopped)

    def test_speed_ignored_while_stopped(self):
        controller, dashboard_client = make_controller()
        feed(controller, FIST, cfg.GESTURE_DEBOUNCE_N)
        dashboard_client.set_speed.reset_mock()

        feed(controller, UP, cfg.GESTURE_DEBOUNCE_N)
        dashboard_client.set_speed.assert_not_called()  # 정지 최우선 — 가속 제스처 무시


if __name__ == "__main__":
    unittest.main()
