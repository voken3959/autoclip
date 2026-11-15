
PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AutoClip Dashboard</title>

<style>
:root {
    color-scheme: dark;
    --bg: #0b0b12;
    --panel: #141420;
    --panel2: #1b1b2a;
    --muted: #9292a8;
    --purple: #9b6cff;
    --green: #35d39a;
    --red: #ff647c;
}

* { box-sizing: border-box; }

body {
    margin: 0;
    background: var(--bg);
    color: #f6f4ff;
    font-family: Inter, system-ui, Segoe UI, sans-serif;
}

.wrap {
    max-width: 1200px;
    margin: auto;
    padding: 28px 22px;
}

.top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 26px;
}

.brand {
    display: flex;
    gap: 12px;
    align-items: center;
    font-size: 22px;
    font-weight: 750;
}

.logo {
    height: 38px;
    width: 38px;
    border-radius: 12px;
    background: linear-gradient(135deg,#a879ff,#6737d8);
    display: grid;
    place-items: center;
    font-size: 19px;
}

.pill {
    border: 1px solid #2b2b3c;
    border-radius: 999px;
    padding: 7px 11px;
    color: var(--muted);
    font-size: 11px;
    letter-spacing: .08em;
}

.dot {
    display: inline-block;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--red);
    margin-right: 7px;
}

h1 {
    font-size: 25px;
    margin: 0 0 5px;
}

.sub {
    color: var(--muted);
    margin-bottom: 22px;
}

.grid {
    display: grid;
    grid-template-columns: repeat(4,1fr);
    gap: 14px;
}

.card {
    background: var(--panel);
    border: 1px solid #222234;
    border-radius: 16px;
    padding: 18px;
    min-width: 0;
}

.label {
    color: var(--muted);
    font-size: 12px;
    margin-bottom: 12px;
}

.value {
    font-size: 30px;
    font-weight: 750;
}

.unit {
    font-size: 12px;
    color: var(--muted);
    font-weight: 500;
}

.layout {
    display: grid;
    grid-template-columns: 1.5fr 1fr;
    gap: 14px;
    margin-top: 14px;
}

.cardhead {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 16px;
    font-weight: 650;
}

canvas {
    width: 100%;
    height: 210px;
    display: block;
}

.bar {
    height: 8px;
    background: #29283a;
    border-radius: 99px;
    overflow: hidden;
    margin-top: 12px;
}

.fill {
    height: 100%;
    width: 0;
    background: linear-gradient(90deg,#7247df,#b28aff);
    transition: width .3s;
}

.muted {
    color: var(--muted);
}

.rows {
    display: flex;
    flex-direction: column;
    gap: 10px;
}

.event {
    background: var(--panel2);
    padding: 12px;
    border-radius: 10px;
    display: flex;
    justify-content: space-between;
    gap: 12px;
}

.event strong {
    display: block;
    margin-bottom: 4px;
}

.event small {
    color: var(--muted);
}

.empty {
    color: var(--muted);
    padding: 25px 0;
    text-align: center;
}

.notice {
    margin-top: 14px;
    padding: 13px 15px;
    border: 1px solid #3b315b;
    background: #171327;
    border-radius: 12px;
    color: #c9b9f5;
    font-size: 12px;
    line-height: 1.5;
}

@media(max-width:850px) {
    .grid { grid-template-columns: repeat(2,1fr); }
    .layout { grid-template-columns: 1fr; }
}

@media(max-width:480px) {
    .wrap { padding: 18px 13px; }
    .grid { gap: 9px; }
    .card { padding: 14px; }
    .value { font-size: 25px; }
}
</style>
</head>

<body>

<main class="wrap">

<header class="top">
    <div class="brand">
        <div class="logo">✂</div>
        AutoClip
    </div>

    <div class="pill">
        <span class="dot" id="dot"></span>
        <span id="connection">CONNECTING</span>
    </div>
</header>

<h1>Stream monitor</h1>

<div class="sub">
    Watching <b id="channel">Jynxzi</b> · Live chat activity and automatic highlight detection
</div>

<section class="grid">

    <div class="card">
        <div class="label">Messages / second</div>
        <div class="value" id="mps">0 <span class="unit">msg/s</span></div>
    </div>

    <div class="card">
        <div class="label">Normal baseline</div>
        <div class="value" id="baseline">— <span class="unit">msg/s</span></div>
    </div>

    <div class="card">
        <div class="label">Hype score</div>
        <div class="value" id="hype">0 <span class="unit">/ 100</span></div>
    </div>

    <div class="card">
        <div class="label">Total messages</div>
        <div class="value" id="total">0</div>
    </div>

</section>

<section class="layout">

    <div class="card">
        <div class="cardhead">
            <span>Chat activity</span>
            <span class="muted" id="phase">WAITING</span>
        </div>

        <canvas id="chart" width="900" height="260"></canvas>

        <div class="muted" style="font-size:12px" id="multiplier">
            Waiting for chat data
        </div>

        <div class="bar">
            <div class="fill" id="calfill"></div>
        </div>

        <div class="muted" style="font-size:11px;margin-top:8px" id="caltext">
            Waiting for calibration
        </div>
    </div>

    <div class="card">
        <div class="cardhead">
            <span>Recent detections</span>
            <span class="muted">Latest first</span>
        </div>

        <div class="rows" id="events">
            <div class="empty">No highlights detected yet.</div>
        </div>
    </div>

</section>

<section class="layout">

    <div class="card">
        <div class="cardhead">
            <span>Recent clips</span>
            <span class="muted" id="clipmode">CLIPPING DISABLED</span>
        </div>

        <div class="rows" id="clips">
            <div class="empty">No clips yet.</div>
        </div>
    </div>

    <div class="card">
        <div class="cardhead">
            <span>Session statistics</span>
        </div>

        <div class="rows">
            <div class="event">
                <span class="muted">Unique chatters</span>
                <strong id="chatters">0</strong>
            </div>

            <div class="event">
                <span class="muted">Calibration</span>
                <strong id="calstate">Not started</strong>
            </div>

            <div class="event">
                <span class="muted">Auto clip requests</span>
                <strong id="autoclip">Off</strong>
            </div>
        </div>
    </div>

</section>

<div class="notice">
    AutoClip monitors Twitch chat activity and compares it against normal
    activity levels to identify potential highlights.
</div>

</main>

<script>
const BACKEND_URL = "https://autoclip-oz6y.onrender.com";

const $ = id => document.getElementById(id);

const canvas = $("chart");
const ctx = canvas.getContext("2d");

let history = [];
let latestRate = 0;
let socket = null;
let reconnectTimer = null;

const MAX_POINTS = 90;

function setConnection(status) {
    $("connection").textContent = status.toUpperCase();

    $("dot").style.background =
        status === "connected" ? "#35d39a" : "#ff647c";
}

function drawGraph() {
    const w = canvas.width;
    const h = canvas.height;

    ctx.clearRect(0,0,w,h);

    // Grid lines
    ctx.strokeStyle = "#29283a";
    ctx.lineWidth = 1;

    for(let i = 0; i <= 4; i++) {
        const y = i * h / 4;

        ctx.beginPath();
        ctx.moveTo(0,y);
        ctx.lineTo(w,y);
        ctx.stroke();
    }

    if(history.length === 0) return;

    const max = Math.max(5,...history) * 1.2;

    // Activity fill
    ctx.beginPath();

    history.forEach((value,index) => {
        const x = index * w / Math.max(MAX_POINTS - 1,1);
        const y = h - (value / max) * (h - 15) - 5;

        if(index === 0) ctx.moveTo(x,y);
        else ctx.lineTo(x,y);
    });

    ctx.lineTo((history.length - 1) * w / Math.max(MAX_POINTS - 1,1),h);
    ctx.lineTo(0,h);
    ctx.closePath();

    ctx.fillStyle = "rgba(155,108,255,0.13)";
    ctx.fill();

    // Activity line
    ctx.beginPath();

    history.forEach((value,index) => {
        const x = index * w / Math.max(MAX_POINTS - 1,1);
        const y = h - (value / max) * (h - 15) - 5;

        if(index === 0) ctx.moveTo(x,y);
        else ctx.lineTo(x,y);
    });

    ctx.strokeStyle = "#a77bff";
    ctx.lineWidth = 3;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.stroke();

    // Latest point
    const lastX = (history.length - 1) * w / Math.max(MAX_POINTS - 1,1);
    const lastY = h - (history[history.length - 1] / max) * (h - 15) - 5;

    ctx.beginPath();
    ctx.arc(lastX,lastY,5,0,Math.PI * 2);
    ctx.fillStyle = "#c4a5ff";
    ctx.fill();
}

// Receive data from backend
function render(s) {
    setConnection("connected");

    $("channel").textContent = s.channel || "jynxzi";

    latestRate = Number(s.messages_per_second) || 0;

    $("mps").innerHTML =
        `${latestRate}<span class="unit"> msg/s</span>`;

    $("baseline").innerHTML =
        `${s.baseline == null ? "—" : s.baseline}<span class="unit"> msg/s</span>`;

    $("hype").innerHTML =
        `${s.hype_score ?? 0}<span class="unit"> / 100</span>`;

    $("total").textContent = s.total_messages ?? 0;

    $("phase").textContent =
        (s.phase || "waiting").toUpperCase();

    $("multiplier").textContent =
        `Current activity: ${s.multiplier ?? 0}× baseline`;

    $("calfill").style.width =
        `${Math.max(0,Math.min(100,s.calibration_progress || 0))}%`;

    $("caltext").textContent =
        s.phase === "calibrating"
        ? `Calibrating: ${s.calibration_elapsed}s / ${s.calibration_total}s`
        : "Calibration complete · monitoring chat spikes";

    $("calstate").textContent =
        s.phase === "calibrating" ? "In progress" : "Complete";

    $("chatters").textContent = s.unique_chatters ?? 0;

    $("autoclip").textContent =
        s.auto_clip_enabled ? "Enabled" : "Off";

    $("clipmode").textContent =
        s.auto_clip_enabled ? "AUTO CLIPPING ON" : "CLIPPING DISABLED";

    // Events
    const events = s.events || [];

    $("events").innerHTML = events.length
        ? events.map(e => `
            <div class="event">
                <div>
                    <strong>Activity spike</strong>
                    <small>${e.time ? new Date(e.time).toLocaleTimeString() : ""}</small>
                </div>
                <strong>${Number(e.multiplier || 0).toFixed(1)}×</strong>
            </div>
        `).join("")
        : '<div class="empty">No highlights detected yet.</div>';

    // Clips
    const clips = s.clips || [];

    $("clips").innerHTML = clips.length
        ? clips.map(c => `
            <div class="event">
                <div>
                    <strong>${escapeHTML(c.title || "Twitch clip")}</strong>
                    <small>${escapeHTML(c.status || "Processing")}</small>
                </div>
                ${c.url ? `<a style="color:#b99aff" href="${escapeHTML(c.url)}" target="_blank" rel="noreferrer">Open ↗</a>` : ""}
            </div>
        `).join("")
        : '<div class="empty">No clips yet.</div>';
}

function escapeHTML(value) {
    return String(value).replace(/[&<>"']/g, char => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
    })[char]);
}

// Update graph every second, even between WebSocket messages.
setInterval(() => {
    history.push(latestRate);

    if(history.length > MAX_POINTS) {
        history.shift();
    }

    drawGraph();
},1000);

// Connect to Render backend
function connect() {
    if(socket && (
        socket.readyState === WebSocket.OPEN ||
        socket.readyState === WebSocket.CONNECTING
    )) return;

    setConnection("connecting");

    const wsUrl =
        BACKEND_URL.replace(/^http/,"ws") + "/ws";

    console.log("Connecting to AutoClip:",wsUrl);

    socket = new WebSocket(wsUrl);

    socket.onopen = () => {
        setConnection("connected");
        console.log("AutoClip connected");

        if(reconnectTimer) {
            clearTimeout(reconnectTimer);
            reconnectTimer = null;
        }
    };

    socket.onmessage = event => {
        try {
            const data = JSON.parse(event.data);
            render(data);
        } catch(error) {
            console.error("Invalid dashboard data:",error);
        }
    };

    socket.onerror = error => {
        console.error("WebSocket error:",error);
    };

    socket.onclose = () => {
        setConnection("disconnected");
        socket = null;

        if(!reconnectTimer) {
            reconnectTimer = setTimeout(() => {
                reconnectTimer = null;
                connect();
            },3000);
        }
    };
}

drawGraph();
connect();
</script>

</body>
</html>"""
