const byId = (id) => document.getElementById(id);

let controlConfigured = false;
let controlReachable = false;
let controlBusy = false;
let roverState = "UNKNOWN";
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
  const enabled = controlConfigured && controlReachable && !controlBusy;
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
    visionCaptureConfigured = Boolean(status.vision_capture_configured);
    const speedInput = byId("target-speed");
    speedInput.max = Number(status.max_target_speed_mps ?? 0.5).toFixed(2);
    speedInput.value = Number(status.default_target_speed_mps ?? 0.25).toFixed(2);
    byId("target-speed-value").textContent = Number(speedInput.value).toFixed(2);
    byId("target-speed-maximum").textContent = `${Number(speedInput.max).toFixed(2)} m/s`;
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

    if (controlConfigured) {
      await loadRoverStatus();
    } else {
      controlReachable = false;
      byId("control-status").textContent = "API 미설정";
      byId("control-status").className = "status-pill neutral";
      showControlResult("");
    }
  } catch (_) {
    controlConfigured = false;
    controlReachable = false;
    byId("control-status").textContent = "API 확인 실패";
    showControlResult("대시보드 서버의 제어 상태를 확인하지 못했습니다.", "error");
  }
  setControlButtons();
}

function applyRoverState(state, targetSpeed = null) {
  roverState = state || "UNKNOWN";
  const numericSpeed = Number(targetSpeed);
  if (state === "RUNNING" && Number.isFinite(numericSpeed) && numericSpeed > 0) {
    const speedInput = byId("target-speed");
    speedInput.value = numericSpeed.toFixed(2);
    byId("target-speed-value").textContent = numericSpeed.toFixed(2);
  }
  const status = byId("control-status");
  if (state === "RUNNING") {
    status.textContent = "운행 중";
    status.className = "status-pill success";
  } else if (state === "STOPPED") {
    status.textContent = "정지";
    status.className = "status-pill neutral";
  } else {
    status.textContent = state === "EMERGENCY" ? "비상 정지" : "상태 확인 필요";
    status.className = "status-pill neutral";
  }
}

async function loadRoverStatus() {
  if (!controlConfigured || controlBusy) return;
  try {
    const response = await fetch("/api/control/status");
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
    controlReachable = Boolean(result.connected);
    applyRoverState(result.state, result.target_speed_mps);
    if (result.state !== "RUNNING" && driveHeartbeatActive) stopDriveHeartbeat();
  } catch (_) {
    controlReachable = false;
    byId("control-status").textContent = "연결 끊김";
    byId("control-status").className = "status-pill neutral";
  }
  setControlButtons();
}

function scheduleWeatherRefresh(intervalSeconds) {
  if (weatherRefreshTimer !== null) clearInterval(weatherRefreshTimer);
  weatherRefreshIntervalSeconds = Math.max(60, intervalSeconds);
  weatherRefreshTimer = setInterval(loadWeather, weatherRefreshIntervalSeconds * 1000);
}

const SVG_NS = "http://www.w3.org/2000/svg";

function makeSvgElement(tag, attributes = {}, text = null) {
  const element = document.createElementNS(SVG_NS, tag);
  Object.entries(attributes).forEach(([name, value]) => element.setAttribute(name, String(value)));
  if (text !== null) element.textContent = text;
  return element;
}

function chartTimeLabel(isoValue) {
  const date = new Date(isoValue);
  if (Number.isNaN(date.getTime())) return "—";
  return `${date.getMonth() + 1}/${date.getDate()} ${String(date.getHours()).padStart(2, "0")}시`;
}

function drawChartGrid(svg, minValue, maxValue, geometry, suffix) {
  const { left, top, width, height } = geometry;
  for (let index = 0; index <= 3; index += 1) {
    const ratio = index / 3;
    const y = top + height * ratio;
    const value = maxValue - (maxValue - minValue) * ratio;
    svg.appendChild(makeSvgElement("line", {
      x1: left, y1: y, x2: left + width, y2: y, class: "chart-grid-line",
    }));
    svg.appendChild(makeSvgElement("text", {
      x: left - 8, y: y + 4, "text-anchor": "end", class: "chart-axis-label",
    }, `${value.toFixed(value % 1 === 0 ? 0 : 1)}${suffix}`));
  }
}

function drawTimeLabels(svg, points, geometry) {
  const { left, top, width, height } = geometry;
  const denominator = Math.max(points.length - 1, 1);
  const step = Math.max(1, Math.ceil(points.length / 8));
  points.forEach((point, index) => {
    if (index % step !== 0 && index !== points.length - 1) return;
    const x = left + (index / denominator) * width;
    svg.appendChild(makeSvgElement("text", {
      x, y: top + height + 20, "text-anchor": "middle", class: "chart-axis-label",
    }, chartTimeLabel(point.observed_at)));
  });
}

function showEmptyChart(svg, message = "시간별 관측 데이터가 없습니다.") {
  svg.replaceChildren(makeSvgElement("text", { x: 360, y: 100, class: "chart-empty" }, message));
}

function renderTemperatureChart(points) {
  const svg = byId("temperature-chart");
  const values = points.map((point) => Number(point.temperature_c));
  const valid = values.filter(Number.isFinite);
  if (!points.length || !valid.length) {
    showEmptyChart(svg);
    return;
  }
  svg.replaceChildren();
  const geometry = { left: 48, top: 14, width: 654, height: 138 };
  const rawMin = Math.min(...valid);
  const rawMax = Math.max(...valid);
  const padding = Math.max(1, (rawMax - rawMin) * 0.15);
  const minValue = Math.floor(rawMin - padding);
  const maxValue = Math.ceil(rawMax + padding);
  const range = Math.max(maxValue - minValue, 1);
  const denominator = Math.max(points.length - 1, 1);
  const coordinates = points.map((point, index) => {
    const value = Number(point.temperature_c);
    if (!Number.isFinite(value)) return null;
    return {
      x: geometry.left + (index / denominator) * geometry.width,
      y: geometry.top + ((maxValue - value) / range) * geometry.height,
      value,
      point,
    };
  }).filter(Boolean);

  const defs = makeSvgElement("defs");
  const gradient = makeSvgElement("linearGradient", {
    id: "temperature-gradient", x1: "0", y1: "0", x2: "0", y2: "1",
  });
  gradient.append(
    makeSvgElement("stop", { offset: "0%", "stop-color": "#f3a25c", "stop-opacity": ".32" }),
    makeSvgElement("stop", { offset: "100%", "stop-color": "#f3a25c", "stop-opacity": "0" }),
  );
  defs.appendChild(gradient);
  svg.appendChild(defs);
  drawChartGrid(svg, minValue, maxValue, geometry, "℃");
  drawTimeLabels(svg, points, geometry);

  const linePath = coordinates.map((coordinate, index) =>
    `${index === 0 ? "M" : "L"}${coordinate.x.toFixed(1)},${coordinate.y.toFixed(1)}`
  ).join(" ");
  if (coordinates.length > 1) {
    const first = coordinates[0];
    const last = coordinates[coordinates.length - 1];
    const baseline = geometry.top + geometry.height;
    svg.appendChild(makeSvgElement("path", {
      d: `${linePath} L${last.x.toFixed(1)},${baseline} L${first.x.toFixed(1)},${baseline} Z`,
      class: "temperature-area",
    }));
  }
  svg.appendChild(makeSvgElement("path", { d: linePath, class: "temperature-line" }));
  coordinates.forEach(({ x, y, value, point }) => {
    const circle = makeSvgElement("circle", { cx: x, cy: y, r: 3.5, class: "temperature-point" });
    circle.appendChild(makeSvgElement("title", {}, `${chartTimeLabel(point.observed_at)} · ${value.toFixed(1)}℃`));
    svg.appendChild(circle);
  });
}

function renderPrecipitationChart(points) {
  const svg = byId("precipitation-chart");
  const values = points.map((point) => Number(point.precipitation_mm));
  const valid = values.filter(Number.isFinite);
  if (!points.length || !valid.length) {
    showEmptyChart(svg);
    return;
  }
  svg.replaceChildren();
  const geometry = { left: 48, top: 14, width: 654, height: 138 };
  const maxValue = Math.max(1, Math.ceil(Math.max(...valid)));
  const slotWidth = geometry.width / Math.max(points.length, 1);
  const barWidth = Math.max(3, slotWidth * 0.62);
  drawChartGrid(svg, 0, maxValue, geometry, "mm");
  drawTimeLabels(svg, points, geometry);
  points.forEach((point, index) => {
    const value = Number(point.precipitation_mm);
    if (!Number.isFinite(value)) return;
    const height = value === 0 ? 2 : (value / maxValue) * geometry.height;
    const x = geometry.left + index * slotWidth + (slotWidth - barWidth) / 2;
    const y = geometry.top + geometry.height - height;
    const bar = makeSvgElement("rect", {
      x, y, width: barWidth, height,
      class: value === 0 ? "precipitation-bar precipitation-zero" : "precipitation-bar",
    });
    const amountLabel = point.precipitation_label || `${value.toFixed(1)}mm`;
    bar.appendChild(makeSvgElement("title", {}, `${chartTimeLabel(point.observed_at)} · ${amountLabel}`));
    svg.appendChild(bar);
  });
}

function renderWeatherCharts(points) {
  const hourlyPoints = Array.isArray(points) ? points.slice(0, 24) : [];
  renderTemperatureChart(hourlyPoints);
  renderPrecipitationChart(hourlyPoints);
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
    renderWeatherCharts(weather.hourly_history);
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
    renderWeatherCharts([]);
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
    controlReachable = true;
    applyRoverState(
      result.rover && result.rover.state,
      result.rover && result.rover.target_speed_mps,
    );
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
      applyRoverState(state, result.rover && result.rover.target_speed_mps);
      showControlResult("차량이 정지 상태입니다.", "error");
    } else {
      controlReachable = true;
      applyRoverState(state, result.rover && result.rover.target_speed_mps);
      showControlResult("");
    }
  } catch (_) {
    // A single failed ping does not mean the vehicle has stopped: the
    // server-side watchdog only stops it after DASHBOARD_HEARTBEAT_TIMEOUT_S
    // (1.5s) of missed heartbeats, several ticks away at this 500ms
    // interval. Keep retrying instead of giving up on one hiccup; the
    // next successful response will report the vehicle's real state.
    showControlResult("통신 재시도 중", "error");
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
byId("target-speed").addEventListener("input", (event) => {
  byId("target-speed-value").textContent = Number(event.target.value).toFixed(2);
});
byId("target-speed").addEventListener("change", () => {
  if (roverState === "RUNNING" && controlReachable && !controlBusy) sendControl("start");
});
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
setInterval(loadRoverStatus, 2000);
