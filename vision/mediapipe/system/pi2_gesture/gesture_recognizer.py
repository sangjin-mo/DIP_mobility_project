# MediaPipe Gesture Recognizer 래퍼
# 참고: https://ai.google.dev/edge/mediapipe/solutions/vision/gesture_recognizer/python
#
# VIDEO 러닝 모드 사용 — 카메라 루프에서 연속으로 들어오는 프레임과 단조증가하는
# 타임스탬프를 그대로 넘기면 되므로, 매 프레임을 독립적으로 취급하는 IMAGE 모드보다
# 이 유스케이스(실시간 카메라 루프)에 적합함.

from __future__ import annotations

import mediapipe as mp

import gesture_config

BaseOptions = mp.tasks.BaseOptions
GestureRecognizer = mp.tasks.vision.GestureRecognizer
GestureRecognizerOptions = mp.tasks.vision.GestureRecognizerOptions
VisionRunningMode = mp.tasks.vision.RunningMode

# gesture_controller가 실제로 다루는 라벨만 화이트리스트로 둠 — 그 외
# (Unknown, Open_Palm, Pointing_Up, Victory, ILoveYou, 손 미검출, 저신뢰도)는
# 전부 "제스처 없음"으로 취급.
_RECOGNIZED_LABELS = {gesture_config.GESTURE_STOP, gesture_config.GESTURE_ACCELERATE, gesture_config.GESTURE_DECELERATE}


class GestureRecognizerWrapper:
    def __init__(self, model_path: str = gesture_config.GESTURE_MODEL_PATH) -> None:
        options = GestureRecognizerOptions(
            base_options=BaseOptions(model_asset_path=model_path),
            running_mode=VisionRunningMode.VIDEO,
        )
        self._recognizer = GestureRecognizer.create_from_options(options)
        # MediaPipe VIDEO 모드는 타임스탬프가 엄격히 증가해야 함 — 호출부가 넘긴
        # 값(예: 경과시간 ms 절삭)이 연속 호출에서 같은 값이 되면 바로 ValueError로
        # 죽어버리는 걸 실측으로 확인함. 여기서 강제로 단조증가시켜 방어한다.
        self._last_timestamp_ms = -1

    def recognize(self, frame_bgr, timestamp_ms: int) -> tuple[str, float] | None:
        """BGR 프레임에서 우리가 다루는 3개 제스처 중 하나를 인식하면 (라벨, confidence) 반환, 아니면 None."""
        # OpenCV는 BGR로 프레임을 주지만 mp.Image는 RGB 계열 포맷을 기대함
        rgb_frame = frame_bgr[:, :, ::-1]
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)

        if timestamp_ms <= self._last_timestamp_ms:
            timestamp_ms = self._last_timestamp_ms + 1
        self._last_timestamp_ms = timestamp_ms

        result = self._recognizer.recognize_for_video(mp_image, timestamp_ms)
        if not result.gestures:
            return None

        top_gesture = result.gestures[0][0]  # 첫 번째 손, 1순위 카테고리
        if top_gesture.category_name not in _RECOGNIZED_LABELS:
            return None
        if top_gesture.score < gesture_config.GESTURE_CONFIDENCE_THRESHOLD:
            return None
        return top_gesture.category_name, top_gesture.score

    def close(self) -> None:
        self._recognizer.close()
