const byId = (id) => document.getElementById(id);

let knownZoneIds = [];
let controlConfigured = false;
let controlBusy = false;
let driveHeartbeatActive = false;
let driveHeartbeatTimer = null;
let weatherRefreshTimer = null;
let weatherRefreshIntervalSeconds = 1800;

const weatherIcons = {
  sunny: "☀️",
  partly_cloudy: "🌤️",
  cloudy: "☁️",
  rain: "🌧️",
  sleet: "🌨️",
  snow: "❄️",
  unknown: "❔",
};

function valueOrDash(value, digits = null) {
  if (value === null || value === undefined) return "—";
  return digits === null ? String(value) : Number(value).toFixed(digits);
}

function steeringLabel(value) {
  if (value === null || value === undefined) return "—";
  if (Math.abs(value) < 0.03) return "직진";
  return `${value < 0 ? "좌" : "우"} ${Math.round(Math.abs(value) * 100)}%`;
}

function renderRoute(currentZone) {
  const route = byId("route");
  let zoneIds = [...knownZoneIds];
  if (currentZone && !zoneIds.includes(currentZone)) zoneIds.push(currentZone);
  zoneIds = zoneIds.filter((id) => Number.isInteger(id) && id > 0).sort((a, b) => a - b);

  if (!zoneIds.length) {
    route.innerHTML = '<span class="route-empty">구역 정보 대기 중</span>';
    return;
  }

  route.replaceChildren();
  zoneIds.forEach((zoneId, index) => {
    if (index) {
      const link = document.createElement("span");
      link.className = "route-link";
      route.appendChild(link);
    }
    const node = document.createElement("span");
    node.className = `route-node${zoneId === currentZone ? " current" : ""}`;
    node.textContent = `${zoneId}구역`;
    route.appendChild(node);
  });
}

function updateLive(snapshot) {
  const dot = byId("connection-dot");
  dot.className = `dot ${snapshot.connected ? "online" : "offline"}`;
  byId("connection-label").textContent = snapshot.connected ? "로버 연결됨" : "로버 연결 끊김";

  const packet = snapshot.telemetry;
  if (!packet) return;

  byId("patrol-id").textContent = packet.patrol_id;
  byId("zone").textContent = packet.zone_id ? `${packet.zone_id}구역` : "이동 구간";
  byId("drive-state").textContent = packet.drive.state;
  byId("speed").textContent = valueOrDash(packet.drive.speed_mps, 2);
  byId("steer").textContent = steeringLabel(packet.drive.steer);
  byId("ultra").textContent = valueOrDash(packet.drive.ultra_cm);
  byId("last-received").textContent = new Date(packet.ts_ms).toLocaleTimeString("ko-KR");
  renderRoute(packet.zone_id);

  if (snapshot.latest_event) {
    const event = snapshot.latest_event;
    byId("latest-event").textContent = `${new Date(event.ts_ms).toLocaleTimeString("ko-KR")} · ${event.type}`;
  }
}

function connectLiveSocket() {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${location.host}/ws/live`);
  socket.onmessage = (message) => updateLive(JSON.parse(message.data));
  socket.onclose = () => setTimeout(connectLiveSocket, 2000);
  socket.onerror = () => socket.close();
}

function setControlButtons() {
  const enabled = controlConfigured && !controlBusy;
  byId("start-drive").disabled = !enabled;
  byId("stop-drive").disabled = !enabled;
  byId("target-speed").disabled = !enabled;
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
    const defaultSpeed = Number(status.default_target_speed_mps ?? 0.25);
    byId("target-speed").value = String(defaultSpeed);
    byId("target-speed-value").textContent = defaultSpeed.toFixed(2);
    byId("control-status").textContent = controlConfigured ? "제어 API 설정됨" : "제어 API 미설정";
    showControlResult(
      controlConfigured
        ? "차량 명령을 보낼 수 있습니다. 실제 상태는 텔레메트리로 확인하세요."
        : "DASHBOARD_ROVER_CONTROL_URL을 설정하면 제어 버튼이 활성화됩니다."
    );
  } catch (_) {
    controlConfigured = false;
    byId("control-status").textContent = "제어 상태 확인 실패";
    showControlResult("대시보드 서버의 제어 상태를 확인하지 못했습니다.", "error");
  }
  setControlButtons();
}

function scheduleWeatherRefresh(intervalSeconds) {
  if (weatherRefreshTimer !== null) clearInterval(weatherRefreshTimer);
  weatherRefreshIntervalSeconds = Math.max(60, intervalSeconds);
  const intervalMs = weatherRefreshIntervalSeconds * 1000;
  weatherRefreshTimer = setInterval(loadWeather, intervalMs);
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
    byId("weather-observed-at").textContent = new Date(weather.observed_at).toLocaleString("ko-KR", {
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
    status.textContent = weather.is_stale ? "갱신 지연" : "정상 수신";
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
  if (command === "start") {
    options.body = JSON.stringify({ target_speed_mps: Number(byId("target-speed").value) });
  }
  try {
    const response = await fetch(`/api/control/${command}`, options);
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
    const roverState = result.rover?.state ? ` · 차량 상태 ${result.rover.state}` : "";
    showControlResult(
      `${result.command} 명령이 차량에서 승인되었습니다${roverState}.`,
      "success"
    );
    if (result.rover?.state) byId("drive-state").textContent = result.rover.state;
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
    showControlResult(
      "차량 heartbeat가 끊겼습니다. 차량 측 watchdog이 자동 정지합니다.",
      "error"
    );
  }
}

async function loadReport(patrolId) {
  const response = await fetch(`/api/patrols/${patrolId}/report`);
  byId("report-content").textContent = response.ok
    ? await response.text()
    : "보고서를 불러오지 못했습니다.";
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

    knownZoneIds = reports[0].zones.map((zone) => zone.zone_id);
    renderRoute(null);

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
byId("target-speed").addEventListener("input", (event) => {
  byId("target-speed-value").textContent = Number(event.target.value).toFixed(2);
});
byId("start-drive").addEventListener("click", () => sendControl("start"));
byId("stop-drive").addEventListener("click", () => sendControl("stop"));
connectLiveSocket();
loadReports();
loadControlStatus();
loadWeather();
