
PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AutoClip</title>

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
    max-width: 1450px;
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
    align-items: center;
    gap: 12px;
    font-size: 22px;
    font-weight: 750;
}

.logo {
    width: 38px;
    height: 38px;
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
    letter-spacing: -1px;
}

.unit {
    font-size: 12px;
    color: var(--muted);
    font-weight: 500;
    letter-spacing: 0;
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
    gap: 10px;
    margin-bottom: 16px;
    font-weight: 650;
}

.muted {
    color: var(--muted);
}

canvas {
    width: 100%;
    height: 220px;
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
    align-items: center;
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

/* Chat */

.chat-card {
    margin-top: 14px;
}

.chat-info {
    display: flex;
    gap: 12px;
    align-items: center;
    color: var(--muted);
    font-size: 11px;
}

.chat-list {
    height: 430px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding-right: 6px;
}

.chat-message {
    padding: 8px 9px;
    border-radius: 8px;
    line-height: 1.55;
    overflow-wrap: anywhere;
    font-size: 13px;
}

.chat-message:hover {
    background: #202031;
}

.chat-user {
    font-weight: 700;
    margin-right: 7px;
}

.chat-content {
    color: #e3e0ee;
}

.seventv-emote {
    display: inline-block;
    width: auto;
    height: 28px;
    max-width: 100px;
    vertical-align: middle;
    object-fit: contain;
    margin: 0 2px;
}

.emote-status {
    color: var(--muted);
    font-size: 11px;
}

@media(max-width:950px) {
    .layout {
        grid-template-columns: 1fr;
    }
}

@media(max-width:650px) {
    .grid {
        grid-template-columns: repeat(2,1fr);
    }
    .wrap {
        padding: 18px 13px;
    }
    .value {
        font-size: 25px;
    }
    .chat-list {
        height: 350px;
    }
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

<!-- Statistics -->

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

<!-- Graph and detections -->

<section class="layout">

    <div class="card">
        <div class="cardhead">
            <span>Chat activity</span>
            <span class="muted" id="phase">WAITING</span>
        </div>

        <canvas id="chart" width="1000" height="280"></canvas>

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

<!-- Chat -->

<section class="card chat-card">

    <div class="cardhead">
        <span>Live Twitch chat</span>

        <div class="chat-info">
            <span id="chatters">0 chatters</span>
            <span>·</span>
            <span id="emotecount">0 7TV emotes</span>
        </div>
    </div>

    <div class="chat-list" id="chat">
        <div class="empty">Waiting for Twitch messages...</div>
    </div>

    <div class="emote-status" id="emotestatus">
        Loading 7TV emotes...
    </div>

</section>

<!-- Clips and session -->

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
                <strong id="uniquechatters">0</strong>
            </div>

            <div class="event">
                <span class="muted">Calibration</span>
                <strong id="calstate">Not started</strong>
            </div>

            <div class="event">
                <span class="muted">Auto clipping</span>
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

let socket = null;
let reconnectTimer = null;

let activityHistory = [];
let lastTotalMessages = null;
let lastChatId = null;

let sevenTVEmotes = {};

const MAX_POINTS = 90;

// ------------------------------
// CONNECTION
// ------------------------------

function setConnection(status) {
    $("connection").textContent = status.toUpperCase();

    $("dot").style.background =
        status === "connected" ? "#35d39a" : "#ff647c";
}

// ------------------------------
// GRAPH
// ------------------------------

const canvas = $("chart");
const ctx = canvas.getContext("2d");

function addGraphPoint(s) {
    const total = Number(s.total_messages) || 0;

    // Prefer the backend's reported rate.
    const rate = Number(s.messages_per_second) || 0;

    // Store a point only when a new backend snapshot arrives.
    activityHistory.push({
        rate: rate,
        total: total,
        time: Date.now()
    });

    if (activityHistory.length > MAX_POINTS) {
        activityHistory.shift();
    }

    lastTotalMessages = total;

    drawGraph();
}

function drawGraph() {
    const w = canvas.width;
    const h = canvas.height;

    ctx.clearRect(0, 0, w, h);

    const padding = 35;
    const graphWidth = w - padding;
    const graphHeight = h - 20;

    const values = activityHistory.map(p => p.rate);
    const max = Math.max(5, ...values) * 1.2;

    // Grid
    ctx.strokeStyle = "#29283a";
    ctx.lineWidth = 1;

    for (let i = 0; i <= 4; i++) {
        const y = i * graphHeight / 4;

        ctx.beginPath();
        ctx.moveTo(padding, y);
        ctx.lineTo(w, y);
        ctx.stroke();

        const label = (max * (1 - i / 4)).toFixed(1);

        ctx.fillStyle = "#9292a8";
        ctx.font = "11px Arial";
        ctx.fillText(label, 2, y + 4);
    }

    if (activityHistory.length < 2) {
        ctx.fillStyle = "#9292a8";
        ctx.font = "13px Arial";
        ctx.fillText("Collecting activity data...", padding + 10, h / 2);
        return;
    }

    const getX = i =>
        padding + (i / (MAX_POINTS - 1)) * graphWidth;

    const getY = value =>
        graphHeight - (value / max) * graphHeight;

    // Filled area
    ctx.beginPath();

    activityHistory.forEach((point, i) => {
        const x = getX(i);
        const y = getY(point.rate);

        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    });

    ctx.lineTo(getX(activityHistory.length - 1), graphHeight);
    ctx.lineTo(getX(0), graphHeight);
    ctx.closePath();

    ctx.fillStyle = "rgba(155,108,255,0.14)";
    ctx.fill();

    // Line
    ctx.beginPath();

    activityHistory.forEach((point, i) => {
        const x = getX(i);
        const y = getY(point.rate);

        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    });

    ctx.strokeStyle = "#a77bff";
    ctx.lineWidth = 3;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.stroke();

    // Current point
    const last = activityHistory[activityHistory.length - 1];

    ctx.beginPath();
    ctx.arc(
        getX(activityHistory.length - 1),
        getY(last.rate),
        5,
        0,
        Math.PI * 2
    );

    ctx.fillStyle = "#d1baff";
    ctx.fill();
}

// ------------------------------
// 7TV EMOTES
// ------------------------------

async function loadSevenTVEmotes() {
    try {
        const response = await fetch(
            BACKEND_URL + "/api/emotes"
        );

        if (!response.ok) {
            throw new Error("HTTP " + response.status);
        }

        sevenTVEmotes = await response.json();

        const count = Object.keys(sevenTVEmotes).length;

        $("emotecount").textContent = count + " 7TV emotes";

        $("emotestatus").textContent =
            count > 0
            ? "7TV emotes loaded"
            : "No 7TV emotes were returned by the backend.";

        renderChat(window.latestChat || []);

    } catch (error) {
        console.error("7TV loading error:", error);

        $("emotestatus").textContent =
            "Could not load 7TV emotes.";
    }
}

// ------------------------------
// CHAT RENDERING
// ------------------------------

function renderMessageContent(container, message) {
    // Match emote-like tokens while preserving punctuation and spaces.
    const parts = message.split(/([A-Za-z0-9_]+)/g);

    for (const part of parts) {
        if (!part) continue;

        const emote = sevenTVEmotes[part];

        if (emote && emote.url) {
            const img = document.createElement("img");

            img.className = "seventv-emote";
            img.src = emote.url;
            img.alt = part;
            img.title = part;
            img.loading = "lazy";

            img.onerror = () => {
                img.replaceWith(document.createTextNode(part));
            };

            container.appendChild(img);

        } else {
            container.appendChild(
                document.createTextNode(part)
            );
        }
    }
}

function renderChat(messages) {
    window.latestChat = messages || [];

    const container = $("chat");
    const items = window.latestChat;

    if (!items.length) {
        container.innerHTML =
            '<div class="empty">Waiting for Twitch messages...</div>';
        return;
    }

    // Only rebuild when a new message arrives or the list changes.
    const newestId = items.length
        ? items[items.length - 1].id
        : null;

    if (
        container.dataset.lastId === String(newestId) &&
        container.children.length === items.length
    ) {
        return;
    }

    container.dataset.lastId = String(newestId);

    container.replaceChildren();

    for (const message of items) {
        const row = document.createElement("div");
        row.className = "chat-message";

        const username = document.createElement("span");
        username.className = "chat-user";
        username.textContent =
            message.display_name || message.username || "Unknown";

        username.style.color = usernameColor(
            message.username || ""
        );

        const content = document.createElement("span");
        content.className = "chat-content";

        renderMessageContent(
            content,
            String(message.text || "").replace(/\r?\n/g, "")
        );

        row.appendChild(username);
        row.appendChild(content);

        container.appendChild(row);
    }

    container.scrollTop = container.scrollHeight;
}

function usernameColor(username) {
    const colors = [
        "#ff7b9c",
        "#a78bfa",
        "#60a5fa",
        "#34d399",
        "#fbbf24",
        "#fb923c",
        "#e879f9",
        "#2dd4bf"
    ];

    let hash = 0;

    for (let i = 0; i < username.length; i++) {
        hash = username.charCodeAt(i) + ((hash << 5) - hash);
    }

    return colors[Math.abs(hash) % colors.length];
}

// ------------------------------
// DASHBOARD DATA
// ------------------------------

function escapeHTML(value) {
    return String(value).replace(/[&<>"']/g, char => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
    })[char]);
}

function render(s) {
    setConnection("connected");

    $("channel").textContent = s.channel || "jynxzi";

    $("mps").innerHTML =
        `${Number(s.messages_per_second || 0).toFixed(2)}<span class="unit"> msg/s</span>`;

    $("baseline").innerHTML =
        `${s.baseline == null ? "—" : Number(s.baseline).toFixed(2)}<span class="unit"> msg/s</span>`;

    $("hype").innerHTML =
        `${s.hype_score ?? 0}<span class="unit"> / 100</span>`;

    $("total").textContent = s.total_messages ?? 0;

    $("phase").textContent =
        (s.phase || "waiting").toUpperCase();

    $("multiplier").textContent =
        `Current activity: ${Number(s.multiplier || 0).toFixed(1)}× baseline`;

    $("calfill").style.width =
        `${Math.max(0, Math.min(100, s.calibration_progress || 0))}%`;

    $("caltext").textContent =
        s.phase === "calibrating"
        ? `Calibrating: ${s.calibration_elapsed}s / ${s.calibration_total}s`
        : "Calibration complete · monitoring chat spikes";

    $("calstate").textContent =
        s.phase === "calibrating" ? "In progress" : "Complete";

    $("chatters").textContent =
        `${s.unique_chatters ?? 0} chatters`;

    $("uniquechatters").textContent =
        s.unique_chatters ?? 0;

    $("autoclip").textContent =
        s.auto_clip_enabled ? "Enabled" : "Off";

    $("clipmode").textContent =
        s.auto_clip_enabled ? "AUTO CLIPPING ON" : "CLIPPING DISABLED";

    // Add a graph point for this actual backend update.
    addGraphPoint(s);

    // Chat
    renderChat(s.chat_messages || []);

    // Events
    const events = s.events || [];

    $("events").replaceChildren();

    if (!events.length) {
        $("events").innerHTML =
            '<div class="empty">No highlights detected yet.</div>';
    } else {
        for (const event of events) {
            const row = document.createElement("div");
            row.className = "event";

            const left = document.createElement("div");

            const title = document.createElement("strong");
            title.textContent = "Activity spike";

            const time = document.createElement("small");
            time.textContent = event.time
                ? new Date(event.time).toLocaleTimeString()
                : "";

            left.append(title, time);

            const score = document.createElement("strong");
            score.textContent =
                `${Number(event.multiplier || 0).toFixed(1)}×`;

            row.append(left, score);
            $("events").appendChild(row);
        }
    }

    // Clips
    const clips = s.clips || [];

    $("clips").replaceChildren();

    if (!clips.length) {
        $("clips").innerHTML =
            '<div class="empty">No clips yet.</div>';
    } else {
        for (const clip of clips) {
            const row = document.createElement("div");
            row.className = "event";

            const left = document.createElement("div");

            const title = document.createElement("strong");
            title.textContent = clip.title || "Twitch clip";

            const status = document.createElement("small");
            status.textContent = clip.status || "Processing";

            left.append(title, status);
            row.appendChild(left);

            if (clip.url) {
                const link = document.createElement("a");
                link.href = clip.url;
                link.target = "_blank";
                link.rel = "noreferrer";
                link.textContent = "Open ↗";
                link.style.color = "#b99aff";

                row.appendChild(link);
            }

            $("clips").appendChild(row);
        }
    }
}

// ------------------------------
// WEBSOCKET
// ------------------------------

function connect() {
    if (
        socket &&
        (
            socket.readyState === WebSocket.OPEN ||
            socket.readyState === WebSocket.CONNECTING
        )
    ) {
        return;
    }

    setConnection("connecting");

    const wsUrl =
        BACKEND_URL.replace(/^http/, "ws") + "/ws";

    console.log("Connecting to AutoClip:", wsUrl);

    socket = new WebSocket(wsUrl);

    socket.onopen = () => {
        setConnection("connected");

        console.log("AutoClip WebSocket connected");

        if (reconnectTimer) {
            clearTimeout(reconnectTimer);
            reconnectTimer = null;
        }
    };

    socket.onmessage = event => {
        try {
            const data = JSON.parse(event.data);
            render(data);
        } catch (error) {
            console.error("Dashboard data error:", error);
        }
    };

    socket.onerror = error => {
        console.error("WebSocket error:", error);
    };

    socket.onclose = () => {
        setConnection("disconnected");

        socket = null;

        if (!reconnectTimer) {
            reconnectTimer = setTimeout(() => {
                reconnectTimer = null;
                connect();
            }, 3000);
        }
    };
}

// ------------------------------
// START
// ------------------------------

window.latestChat = [];

drawGraph();
loadSevenTVEmotes();
connect();

</script>
</body>
</html>"""
