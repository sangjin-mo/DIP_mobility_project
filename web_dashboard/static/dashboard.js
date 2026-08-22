const byId = (id) => document.getElementById(id);

let controlConfigured = false;
let controlBusy = false;
let visionCaptureConfigured = false;
let driveHeartbeatActive = false;
let driveHeartbeatTimer = null;
let weatherRefreshTimer = null;
let weatherRefreshIntervalSeconds = 1800;
const dashboardSlides = [...document.querySelectorAll("[data-dashboard-slide]")];
let currentSlideIndex = 0;

const weatherIcons = {
  sunny: "☀️", partly_cloudy: "🌤️", cloudy: "☁️", rain: "🌧️",
  sleet: "🌨️", snow: "❄️", unknown: "❔",
};

function valueOrDash(value, digits = null) {
  if (value === null || value === undefined) return "—";
  return digits === null ? String(value) : Number(value).toFixed(digits);
}

function updateWeatherAdvice(weather) {
  const precipitation = Number(weather.precipitation_mm);
  const temperature = Number(weather.temperature_c);
  byId("rain-advice").textContent = Number.isFinite(precipitation)
    ? precipitation > 5 ? "우산을 챙기세요." : "별도 사항 없음"
    : "강우량을 확인할 수 없습니다.";
  byId("temperature-advice").textContent = !Number.isFinite(temperature)
    ? "온도를 확인할 수 없습니다."
    : temperature <= 30 ? "야외활동 하기 좋은 날씨입니다."
    : temperature < 35 ? "온열 질환에 유의하세요."
    : "야외 활동을 자제하세요.";
}

function setControlButtons() {
  const enabled = controlConfigured && !controlBusy;
  byId("start-drive").disabled = !enabled;
  byId("stop-drive").disabled = !enabled;
}

function showControlResult(message, kind = "") {
  const result = byId("control-result");
  result.textContent = message;
  result.className = `notice ${kind}`.trim();
}

async function loadControlStatus() {
  try {
    const response = await fetch("/api/status");
    const status = await response.json();
    controlConfigured = Boolean(status.control_configured);
    visionCaptureConfigured = Boolean(status.vision_capture_configured);
    scheduleWeatherRefresh(Number(status.weather_refresh_interval_s ?? 1800));

    const cameraStatus = byId("camera-status");
    cameraStatus.textContent = status.camera_configured ? "촬영 API 연결" : "미연결";
    cameraStatus.className = `status-pill ${status.camera_configured ? "success" : "neutral"}`;
    byId("capture-image").disabled = !status.camera_configured;
    byId("capture-result").textContent = status.camera_configured
      ? visionCaptureConfigured
        ? "촬영 버튼을 누르면 웹캠 Pi의 최신 정지 이미지를 전송받습니다."
        : "촬영 버튼을 누르면 설정된 정지 이미지를 다시 불러옵니다."
      : "";
    if (visionCaptureConfigured) loadLatestStillImage();

    const controlStatus = byId("control-status");
    controlStatus.textContent = controlConfigured ? "제어 가능" : "API 미설정";
    controlStatus.className = `status-pill ${controlConfigured ? "success" : "neutral"}`;
    showControlResult(controlConfigured
      ? "차량 명령을 보낼 수 있습니다."
      : "DASHBOARD_ROVER_CONTROL_URL을 설정하면 버튼이 활성화됩니다.");
  } catch (_) {
    controlConfigured = false;
    byId("control-status").textContent = "API 확인 실패";
    showControlResult("대시보드 서버의 제어 상태를 확인하지 못했습니다.", "error");
  }
  setControlButtons();
}

function scheduleWeatherRefresh(intervalSeconds) {
  if (weatherRefreshTimer !== null) clearInterval(weatherRefreshTimer);
  weatherRefreshIntervalSeconds = Math.max(60, intervalSeconds);
  weatherRefreshTimer = setInterval(loadWeather, weatherRefreshIntervalSeconds * 1000);
}

async function loadWeather(forceRefresh = false) {
  const status = byId("weather-status");
  const refreshButton = byId("refresh-weather");
  refreshButton.disabled = true;
  if (forceRefresh) status.textContent = "새 날씨 조회 중";
  try {
    const response = await fetch(forceRefresh ? "/api/weather/refresh" : "/api/weather", {
      method: forceRefresh ? "POST" : "GET",
    });
    const weather = await response.json();
    if (!response.ok) throw new Error(weather.detail || `HTTP ${response.status}`);
    byId("weather-temperature").textContent = valueOrDash(weather.temperature_c, 1);
    byId("weather-humidity").textContent = valueOrDash(weather.humidity_percent, 0);
    byId("weather-condition").textContent = weather.weather || "확인 불가";
    const icon = byId("weather-icon");
    icon.textContent = weatherIcons[weather.weather_icon] || weatherIcons.unknown;
    icon.setAttribute("aria-label", weather.weather || "날씨 확인 불가");
    byId("weather-rain").textContent = weather.is_raining ? "있음" : "없음";
    byId("weather-rain-probability").textContent = valueOrDash(weather.rain_probability_percent, 0);
    byId("weather-precipitation").textContent = valueOrDash(weather.precipitation_mm, 1);
    byId("weather-wind").textContent = valueOrDash(weather.wind_speed_mps, 1);
    updateWeatherAdvice(weather);
    byId("weather-observed-at").textContent = new Date(weather.observed_at).toLocaleString("ko-KR", {
      month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
    byId("weather-fetched-at").textContent = new Date(weather.fetched_at).toLocaleTimeString("ko-KR", {
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
    status.textContent = weather.is_stale ? "갱신 지연" : forceRefresh ? "새로고침 완료" : "정상 수신";
    status.className = weather.is_stale ? "muted warning" : "muted success-text";
    byId("weather-source").textContent = weather.is_stale
      ? `마지막 정상 관측값 표시 중 · ${weather.error || "기상청 API 응답 지연"}`
      : weather.location || "";
  } catch (error) {
    status.textContent = "설정 또는 연결 확인 필요";
    status.className = "muted warning";
    byId("weather-source").textContent = error.message;
  } finally {
    refreshButton.disabled = false;
  }
}

async function sendControl(command) {
  if (command !== "start") stopDriveHeartbeat();
  controlBusy = true;
  setControlButtons();
  showControlResult("차량 응답을 기다리는 중입니다.");
  const options = { method: "POST", headers: { "Content-Type": "application/json" } };
  if (command === "start") options.body = JSON.stringify({});
  try {
    const response = await fetch(`/api/control/${command}`, options);
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
    showControlResult(`${command === "start" ? "운행" : "정지"} 명령이 차량에서 승인되었습니다.`, "success");
    if (command === "start") startDriveHeartbeat();
  } catch (error) {
    showControlResult(`명령 실패: ${error.message}`, "error");
  } finally {
    controlBusy = false;
    setControlButtons();
  }
}

function startDriveHeartbeat() {
  driveHeartbeatActive = true;
  if (driveHeartbeatTimer !== null) clearInterval(driveHeartbeatTimer);
  driveHeartbeatTimer = setInterval(sendDriveHeartbeat, 500);
}

function stopDriveHeartbeat() {
  driveHeartbeatActive = false;
  if (driveHeartbeatTimer !== null) {
    clearInterval(driveHeartbeatTimer);
    driveHeartbeatTimer = null;
  }
}

async function sendDriveHeartbeat() {
  if (!driveHeartbeatActive) return;
  try {
    const response = await fetch("/api/control/heartbeat", { method: "POST" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
    const state = result.rover && result.rover.state;
    if (state !== "RUNNING") {
      // Vehicle is no longer running (watchdog timeout elsewhere, or STOP
      // issued from another client) - nothing left to keep alive.
      stopDriveHeartbeat();
      showControlResult("차량이 정지 상태입니다.", "error");
    } else {
      showControlResult("정지는 throttle을 즉시 0으로 만듭니다.", "");
    }
  } catch (_) {
    // A single failed ping does not mean the vehicle has stopped: the
    // server-side watchdog only stops it after DASHBOARD_HEARTBEAT_TIMEOUT_S
    // (1.5s) of missed heartbeats, several ticks away at this 500ms
    // interval. Keep retrying instead of giving up on one hiccup; the
    // next successful response will report the vehicle's real state.
    showControlResult("통신 재시도 중... 계속 끊기면 watchdog이 자동 정지합니다.", "error");
  }
}

function showStillImage(url) {
  const image = byId("camera-image");
  const refreshedUrl = new URL(url, window.location.href);
  refreshedUrl.searchParams.set("captured_at", Date.now());
  image.src = refreshedUrl.toString();
  byId("camera-still").hidden = false;
  byId("camera-placeholder").hidden = true;
}

async function loadLatestStillImage() {
  try {
    const response = await fetch("/api/camera/latest");
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
    if (result.available) {
      showStillImage(result.image.image_url);
      byId("capture-result").textContent = `최근 전송 이미지: ${result.image.filename || "파일명 없음"}`;
    }
  } catch (error) {
    byId("capture-result").textContent = `최근 이미지 확인 실패: ${error.message}`;
  }
}

async function refreshStillImage() {
  const button = byId("capture-image");
  button.disabled = true;
  byId("capture-result").textContent = "웹캠 이미지 전송을 요청하고 있습니다.";
  try {
    if (!visionCaptureConfigured) {
      const image = byId("camera-image");
      if (!image.src) throw new Error("촬영 API가 설정되지 않았습니다.");
      showStillImage(image.src);
      byId("capture-result").textContent = "최신 정지 이미지를 다시 불러왔습니다.";
      return;
    }
    const response = await fetch("/api/camera/capture", { method: "POST" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
    showStillImage(result.image.image_url);
    const transfer = result.image.transfer || {};
    byId("capture-result").textContent = transfer.requested === 0
      ? `새 전송 대상이 없어 기존 최신 이미지를 표시합니다: ${result.image.filename || "파일명 없음"}`
      : `촬영 이미지 수신 완료: ${result.image.filename || "파일명 없음"}`;
  } catch (error) {
    byId("capture-result").textContent = `촬영 실패: ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

function observationSummary(observations) {
  const rows = [];
  Object.entries(observations || {}).forEach(([crop, states]) => {
    const counts = Object.entries(states || {}).map(([state, count]) => `${state} ${count}`).join(", ");
    rows.push(`${crop}: ${counts || "관측 없음"}`);
  });
  return rows.join(" · ") || "관측 데이터 없음";
}

async function loadCropReport() {
  const button = byId("refresh-report");
  const status = byId("crop-report-status");
  button.disabled = true;
  status.textContent = "불러오는 중";
  try {
    const response = await fetch("/api/crop-report/latest");
    const report = await response.json();
    if (!response.ok) throw new Error(report.detail || `HTTP ${response.status}`);
    const grid = byId("crop-grid");
    grid.replaceChildren();
    if (!report.available) {
      const empty = document.createElement("article");
      empty.className = "crop-card crop-empty";
      empty.textContent = "AI/LLM 파이프라인에서 생성된 레포트가 없습니다.";
      grid.appendChild(empty);
      byId("llm-report").textContent = "생성된 레포트가 없습니다.";
      byId("report-generated-at").textContent = "—";
      status.textContent = "레포트 없음";
      status.className = "status-pill neutral";
      return;
    }
    report.zones.forEach((zone) => {
      const card = document.createElement("article");
      card.className = "crop-card";
      const title = document.createElement("div");
      title.className = "crop-title";
      const name = document.createElement("strong");
      name.textContent = `${zone.label}구역 · ${zone.zone_name || `구역 ${zone.zone_id}`}`;
      const state = document.createElement("span");
      state.className = "crop-state";
      state.textContent = zone.status || "판정 전";
      title.append(name, state);
      const summary = document.createElement("p");
      summary.className = "crop-observations";
      summary.textContent = observationSummary(zone.observations);
      card.append(title, summary);
      grid.appendChild(card);
    });
    byId("llm-report").textContent = report.report_markdown || "레포트 본문이 없습니다.";
    byId("report-generated-at").textContent = report.generated_at
      ? new Date(report.generated_at).toLocaleString("ko-KR") : report.patrol_id;
    status.textContent = report.llm_enabled ? "LLM 레포트" : "규칙 기반 레포트";
    status.className = "status-pill success";
  } catch (error) {
    status.textContent = "조회 실패";
    status.className = "status-pill neutral";
    byId("llm-report").textContent = `레포트 조회 실패: ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

function showDashboardSlide(index) {
  currentSlideIndex = (index + dashboardSlides.length) % dashboardSlides.length;
  dashboardSlides.forEach((slide, slideIndex) => {
    const isActive = slideIndex === currentSlideIndex;
    slide.hidden = !isActive;
    slide.classList.toggle("is-active", isActive);
    slide.setAttribute("aria-hidden", String(!isActive));
  });
  byId("current-slide").textContent = String(currentSlideIndex + 1);
  byId("total-slides").textContent = String(dashboardSlides.length);
}

function toggleWorkAction(button) {
  const isActive = button.getAttribute("aria-pressed") === "true";
  button.setAttribute("aria-pressed", String(!isActive));
}

byId("refresh-weather").addEventListener("click", () => loadWeather(true));
byId("capture-image").addEventListener("click", refreshStillImage);
byId("refresh-report").addEventListener("click", loadCropReport);
byId("start-drive").addEventListener("click", () => sendControl("start"));
byId("stop-drive").addEventListener("click", () => sendControl("stop"));
byId("previous-slide").addEventListener("click", () => showDashboardSlide(currentSlideIndex - 1));
byId("next-slide").addEventListener("click", () => showDashboardSlide(currentSlideIndex + 1));
document.querySelectorAll(".work-toggle").forEach((button) => {
  button.addEventListener("click", () => toggleWorkAction(button));
});
document.addEventListener("keydown", (event) => {
  if (event.key === "ArrowLeft") showDashboardSlide(currentSlideIndex - 1);
  if (event.key === "ArrowRight") showDashboardSlide(currentSlideIndex + 1);
});
showDashboardSlide(0);
loadControlStatus();
loadWeather();
loadCropReport();
