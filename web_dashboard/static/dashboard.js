const byId = (id) => document.getElementById(id);

let knownZoneIds = [];

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
  byId("temperature").textContent = valueOrDash(packet.env.temp_c, 1);
  byId("humidity").textContent = valueOrDash(packet.env.humid_pct, 1);
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
connectLiveSocket();
loadReports();
