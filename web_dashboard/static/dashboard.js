const byId = (id) => document.getElementById(id);

let controlConfigured = false;
let controlBusy = false;
let driveHeartbeatActive = false;
let driveHeartbeatTimer = null;
let weatherRefreshTimer = null;
let weatherRefreshIntervalSeconds = 1800;

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
    scheduleWeatherRefresh(Number(status.weather_refresh_interval_s ?? 1800));

    const cameraStatus = byId("camera-status");
    cameraStatus.textContent = status.camera_configured ? "촬영 API 연결" : "미연결";
    cameraStatus.className = `status-pill ${status.camera_configured ? "success" : "neutral"}`;
    byId("capture-image").disabled = !status.camera_configured;
    byId("capture-result").textContent = status.camera_configured
      ? "촬영 버튼을 누르면 최신 정지 이미지를 다시 불러옵니다."
      : "비전팀 촬영 API 연동 대기 중입니다.";

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
      : `${weather.location || "위치 미설정"} · ${weather.source} · ${Math.round(weatherRefreshIntervalSeconds / 60)}분마다 자동 갱신`;
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
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
  } catch (_) {
    stopDriveHeartbeat();
    showControlResult("차량 연결이 끊겼습니다. 차량 측 watchdog이 자동 정지합니다.", "error");
  }
}

function refreshStillImage() {
  const image = byId("camera-image");
  if (!image) return;
  const url = new URL(image.src);
  url.searchParams.set("captured_at", Date.now());
  image.src = url.toString();
  byId("capture-result").textContent = "최신 정지 이미지를 요청했습니다.";
}

async function loadReport(patrolId) {
  const response = await fetch(`/api/patrols/${patrolId}/report`);
  byId("report-content").textContent = response.ok ? await response.text() : "보고서를 불러오지 못했습니다.";
}

async function loadReports() {
  const list = byId("report-list");
  list.innerHTML = '<p class="muted">보고서 조회 중</p>';
  try {
    const response = await fetch("/api/patrols");
    const reports = await response.json();
    list.replaceChildren();
    if (!reports.length) {
      list.innerHTML = '<p class="muted">생성된 보고서가 없습니다.</p>';
      return;
    }
    reports.forEach((report) => {
      const button = document.createElement("button");
      button.className = "report-item";
      const title = document.createElement("strong");
      title.textContent = report.patrol_id;
      const detail = document.createElement("span");
      detail.textContent = `${report.patrol_date} · ${report.overall_status}`;
      button.append(title, detail);
      button.addEventListener("click", () => loadReport(report.patrol_id));
      list.appendChild(button);
    });
  } catch (_) {
    list.innerHTML = '<p class="muted">보고서 서버에 연결할 수 없습니다.</p>';
  }
}

byId("refresh-reports").addEventListener("click", loadReports);
byId("refresh-weather").addEventListener("click", () => loadWeather(true));
byId("capture-image").addEventListener("click", refreshStillImage);
byId("start-drive").addEventListener("click", () => sendControl("start"));
byId("stop-drive").addEventListener("click", () => sendControl("stop"));
loadReports();
loadControlStatus();
loadWeather();
