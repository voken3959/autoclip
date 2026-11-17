"use strict";

(() => {

// Served by the backend, so everything talks to the same address. Only when the
// page is opened straight from disk does it fall back to the hosted backend.
const BACKEND = location.protocol.indexOf("http") === 0
    ? location.origin
    : "https://autoclip-oz6y.onrender.com";

const $ = id => document.getElementById(id);
const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
const reduced = () => motion.matches;
const IS_LOCAL = ["localhost", "127.0.0.1", "[::1]", "::1"].indexOf(location.hostname) !== -1;

const UI_FONT = '"IBM Plex Sans", ui-sans-serif, system-ui, sans-serif';
const DISPLAY_FONT = '"Bricolage Grotesque", ui-sans-serif, system-ui, sans-serif';

const ICON = {
    film: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="3"/><path d="M10 9.5v5l4.5-2.5z" fill="currentColor"/></svg>',
    warn: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5M12 16.5v.01"/></svg>',
    check: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
    down: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 5v14M6 13l6 6 6-6"/></svg>',
};

const S = {
    snap: null,          // latest snapshot from the server
    wsOpen: false,
    lastAt: 0,           // when the last snapshot arrived
    seenEvent: null,     // newest spike we have already announced
    clipStatus: new Map(),
    first: true,
    clipBusy: false,
    autoBusy: false,
};

// ---------------------------------------------------------------- helpers

function fmt(value, decimals) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
    return Number(value).toLocaleString(undefined, {
        minimumFractionDigits: decimals || 0,
        maximumFractionDigits: decimals || 0,
    });
}

// Counts a number up (or down) to its new value instead of jumping.
function tween(el, to, decimals, ms) {
    if (to === null || to === undefined || Number.isNaN(Number(to))) {
        if (el._raf) cancelAnimationFrame(el._raf);
        el._v = null;
        el.textContent = "—";
        return;
    }
    to = Number(to);
    const from = el._v === null || el._v === undefined ? 0 : el._v;
    if (el._raf) cancelAnimationFrame(el._raf);

    if (reduced() || from === to) {
        el._v = to;
        el.textContent = fmt(to, decimals);
        return;
    }

    const start = performance.now();
    const duration = ms || 800;
    const step = now => {
        const p = Math.min(1, (now - start) / duration);
        const eased = 1 - Math.pow(1 - p, 3);
        el._v = from + (to - from) * eased;
        el.textContent = fmt(el._v, decimals);
        if (p < 1) {
            el._raf = requestAnimationFrame(step);
        } else {
            el._v = to;
            el.textContent = fmt(to, decimals);
        }
    };
    el._raf = requestAnimationFrame(step);
}

function agoText(seconds) {
    if (seconds === null || seconds === undefined) return "";
    if (seconds < 10) return "just now";
    if (seconds < 60) return Math.floor(seconds) + "s ago";
    if (seconds < 3600) return Math.floor(seconds / 60) + "m ago";
    return Math.floor(seconds / 3600) + "h ago";
}

function ageOf(item) {
    if (item.ago !== null && item.ago !== undefined) return item.ago;
    const then = Date.parse(item.time);
    return Number.isNaN(then) ? null : Math.max(0, (Date.now() - then) / 1000);
}

function safeUrl(url) {
    return typeof url === "string" && url.indexOf("https://") === 0 ? url : "";
}

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

function restartAnimation(node, className) {
    node.classList.remove(className);
    void node.offsetWidth;
    node.classList.add(className);
}

async function copyText(text) {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch (error) {
        const area = document.createElement("textarea");
        area.value = text;
        area.style.cssText = "position:fixed;opacity:0;top:0;left:0";
        document.body.appendChild(area);
        area.select();
        let ok = false;
        try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
        area.remove();
        return ok;
    }
}

function flash(button, text) {
    const original = button.dataset.label || button.textContent;
    button.dataset.label = original;
    button.textContent = text;
    clearTimeout(button._flash);
    button._flash = setTimeout(() => { button.textContent = original; }, 1600);
}

// ----------------------------------------------------------------- toasts

const recentToasts = new Map();

function dismissToast(node) {
    if (!node.isConnected || node.classList.contains("leaving")) return;
    node.classList.add("leaving");
    setTimeout(() => node.remove(), 300);
}

function toast(message, kind, options) {
    options = options || {};
    const key = kind + message;
    const now = Date.now();
    if (recentToasts.get(key) > now - 3000) return null;
    recentToasts.set(key, now);

    const host = $("toasts");
    const node = el("div", "toast " + (kind || ""));
    node.setAttribute("role", kind === "err" ? "alert" : "status");
    node.appendChild(el("div", "msg", message));

    const link = safeUrl(options.link);
    if (link) {
        const a = el("a", "", options.linkText || "Open");
        a.href = link;
        a.target = "_blank";
        a.rel = "noopener";
        node.appendChild(a);
    }

    host.appendChild(node);
    while (host.children.length > 4) dismissToast(host.firstElementChild);

    const timer = setTimeout(
        () => dismissToast(node),
        options.ttl || (kind === "err" ? 9000 : 5500)
    );
    node.addEventListener("click", event => {
        if (event.target.closest("a")) return;
        clearTimeout(timer);
        dismissToast(node);
    });
    return node;
}

// ---------------------------------------------------------------- dialogs

function openSheet(dialog) {
    if (!dialog.open) dialog.showModal();
}

function closeSheet(dialog) {
    if (!dialog.open || dialog.classList.contains("closing")) return;
    dialog.classList.add("closing");
    setTimeout(() => {
        dialog.classList.remove("closing");
        dialog.close();
    }, reduced() ? 0 : 190);
}

for (const dialog of document.querySelectorAll("dialog.sheet")) {
    dialog.addEventListener("cancel", event => {
        event.preventDefault();
        closeSheet(dialog);
    });
    // A click on the dimmed backdrop lands on the dialog element itself.
    dialog.addEventListener("click", event => {
        if (event.target !== dialog) return;
        const box = dialog.getBoundingClientRect();
        const inside =
            event.clientX >= box.left && event.clientX <= box.right &&
            event.clientY >= box.top && event.clientY <= box.bottom;
        if (!inside) closeSheet(dialog);
    });
}

// -------------------------------------------------------------- admin key

const KEY_NAME = "autoclip.adminKey";

function getKey() {
    try { return sessionStorage.getItem(KEY_NAME) || ""; } catch (e) { return ""; }
}

function setKey(value) {
    try {
        if (value) sessionStorage.setItem(KEY_NAME, value);
        else sessionStorage.removeItem(KEY_NAME);
    } catch (e) { /* storage can be unavailable; the key is then asked for again */ }
}

let keyPrompt = null;

function askKey(problem) {
    if (keyPrompt) return keyPrompt;

    keyPrompt = new Promise(resolve => {
        const dialog = $("keyDialog");
        const form = $("keyForm");
        const input = $("keyInput");
        const error = $("keyError");
        const submit = $("keyOk");
        let settled = false;

        const showError = message => {
            error.textContent = message;
            error.hidden = false;
            restartAnimation(error, "shake");
        };

        const finish = value => {
            if (settled) return;
            settled = true;
            keyPrompt = null;
            form.onsubmit = null;
            $("keyCancel").onclick = null;
            dialog.removeEventListener("close", onClose);
            resolve(value);
        };
        const onClose = () => finish(null);

        input.value = "";
        error.hidden = !problem;
        error.textContent = problem || "";
        dialog.addEventListener("close", onClose);
        $("keyCancel").onclick = () => closeSheet(dialog);

        form.onsubmit = async event => {
            event.preventDefault();
            const key = input.value.trim();
            if (!key) return;
            submit.disabled = true;
            try {
                const response = await fetch(BACKEND + "/api/admin/check", {
                    method: "POST",
                    headers: { "X-Admin-Key": key },
                });
                if (response.ok) {
                    setKey(key);
                    finish(key);
                    closeSheet(dialog);
                    return;
                }
                const data = await response.json().catch(() => ({}));
                if (data.code === "admin_key_not_set") {
                    finish(null);
                    closeSheet(dialog);
                    toast(data.error || "Set ADMIN_KEY on the server first.", "err");
                    return;
                }
                showError(data.error || "That admin key isn't right.");
            } catch (e) {
                showError("Couldn't reach the server. Try again.");
            } finally {
                submit.disabled = false;
            }
        };

        openSheet(dialog);
        setTimeout(() => input.focus(), 60);
    });

    return keyPrompt;
}

// Calls a protected endpoint. Asks for the admin key when the server wants one.
async function api(path, body, raw) {
    for (let attempt = 0; attempt < 3; attempt++) {
        const headers = { "Content-Type": "application/json" };
        const key = getKey();
        if (key) headers["X-Admin-Key"] = key;

        let response;
        try {
            response = await fetch(BACKEND + path, {
                method: "POST",
                headers: headers,
                body: JSON.stringify(body || {}),
            });
        } catch (e) {
            return { ok: false, code: "network", error: "Couldn't reach the server. Check your connection and try again." };
        }

        // `raw` hands back a successful response untouched (used for downloads).
        if (raw && response.ok) return { ok: true, response: response };

        let data = {};
        try { data = await response.json(); } catch (e) { data = {}; }

        if (response.status === 401 && data.code === "bad_admin_key") {
            setKey("");
            const entered = await askKey(key ? "That admin key isn't right." : "");
            if (!entered) return { ok: false, code: "cancelled", error: "" };
            continue;
        }

        data.ok = response.ok && data.ok !== false;
        data.status = response.status;
        return data;
    }
    return { ok: false, code: "bad_admin_key", error: "That admin key isn't right." };
}

function apiFailed(result) {
    if (!result || result.code === "cancelled") return;
    toast(result.error || "Something went wrong. Try again.", "err");
    if (result.code === "admin_key_not_set") openSetup();
}

// -------------------------------------------------------------- the trace

const trace = (() => {
    const wrap = $("trace");
    const canvas = $("canvas");
    const ctx = canvas.getContext("2d");
    const tip = $("tip");
    const tipValue = el("b");
    const tipText = el("span");
    tip.append(tipValue, tipText);

    const styles = getComputedStyle(document.documentElement);
    const token = name => styles.getPropertyValue(name).trim();
    const C = {
        lav: token("--lav"),
        hot: token("--hot"),
        ok: token("--ok"),
        amber: token("--amber"),
        text: token("--text"),
        muted: token("--muted"),
        faint: token("--faint"),
        line: token("--line"),
        soft: token("--line-soft"),
        raise: token("--raise"),
    };

    const SAMPLES = 120;                      // two minutes of one-second samples
    const pad = { l: 10, r: 12, t: 32, b: 26 };

    let W = 1, H = 1;
    let hist = [], base = null, thr = null, events = [], clips = [];
    let tick = null;
    let offset = 0;        // how far the picture still has to slide left, in samples
    let yMax = 8;
    let reveal = 0;
    let hasData = false;
    let hoverX = null;
    let last = performance.now();

    function hexA(hex, alpha) {
        let h = hex.replace("#", "");
        if (h.length === 3) h = h.split("").map(c => c + c).join("");
        const n = parseInt(h, 16);
        return "rgba(" + (n >> 16 & 255) + "," + (n >> 8 & 255) + "," + (n & 255) + "," + alpha + ")";
    }

    function resize() {
        const box = wrap.getBoundingClientRect();
        W = Math.max(1, Math.round(box.width));
        H = Math.max(1, Math.round(box.height));
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = Math.round(W * dpr);
        canvas.height = Math.round(H * dpr);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function roundRect(x, y, w, h, r) {
        ctx.beginPath();
        ctx.moveTo(x + r, y);
        ctx.arcTo(x + w, y, x + w, y + h, r);
        ctx.arcTo(x + w, y + h, x, y + h, r);
        ctx.arcTo(x, y + h, x, y, r);
        ctx.arcTo(x, y, x + w, y, r);
        ctx.closePath();
    }

    // Smooth line through the points that never overshoots a peak.
    function curve(points) {
        const n = points.length;
        if (n < 2) return;
        const dx = [], slope = [], tangent = new Array(n);
        for (let i = 0; i < n - 1; i++) {
            dx[i] = points[i + 1].x - points[i].x;
            slope[i] = (points[i + 1].y - points[i].y) / dx[i];
        }
        tangent[0] = slope[0];
        tangent[n - 1] = slope[n - 2];
        for (let i = 1; i < n - 1; i++) {
            tangent[i] = slope[i - 1] * slope[i] <= 0 ? 0 : (slope[i - 1] + slope[i]) / 2;
        }
        for (let i = 0; i < n - 1; i++) {
            if (slope[i] === 0) {
                tangent[i] = 0;
                tangent[i + 1] = 0;
                continue;
            }
            const a = tangent[i] / slope[i];
            const b = tangent[i + 1] / slope[i];
            const s = a * a + b * b;
            if (s > 9) {
                const tau = 3 / Math.sqrt(s);
                tangent[i] = tau * a * slope[i];
                tangent[i + 1] = tau * b * slope[i];
            }
        }
        ctx.moveTo(points[0].x, points[0].y);
        for (let i = 0; i < n - 1; i++) {
            const h = dx[i];
            ctx.bezierCurveTo(
                points[i].x + h / 3, points[i].y + tangent[i] * h / 3,
                points[i + 1].x - h / 3, points[i + 1].y - tangent[i + 1] * h / 3,
                points[i + 1].x, points[i + 1].y
            );
        }
    }

    function update(s) {
        const incoming = s.history || [];
        if (s.tick !== undefined && s.tick !== null && tick !== null) {
            const gained = s.tick - tick;
            if (gained > 0) offset += gained;
            else if (gained < 0) offset = 0;      // server restarted
        } else if (!hasData || s.tick === undefined) {
            offset += 1;
        }
        offset = document.hidden ? 0 : Math.min(offset, 2.5);
        if (s.tick !== undefined && s.tick !== null) tick = s.tick;

        hist = incoming;
        base = s.baseline;
        thr = s.threshold;
        events = s.events || [];
        clips = s.clips || [];

        if (!hasData && incoming.length > 1) {
            hasData = true;
            reveal = reduced() ? 1 : 0;
        }

        const latest = incoming.length ? incoming[incoming.length - 1] : 0;
        canvas.setAttribute(
            "aria-label",
            "Chat activity over the last two minutes. Latest second: " + latest + " messages."
        );
    }

    function draw() {
        ctx.clearRect(0, 0, W, H);

        const left = pad.l;
        const right = W - pad.r;
        const top = pad.t;
        const bottom = H - pad.b;
        const step = (right - left) / (SAMPLES - 1);
        const yOf = value => bottom - Math.min(value, yMax * 1.02) / yMax * (bottom - top);
        const n = hist.length;
        const ease = 1 - Math.pow(1 - reveal, 3);

        // Time axis
        ctx.strokeStyle = C.soft;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(left, bottom + 0.5);
        ctx.lineTo(W - pad.r + 8, bottom + 0.5);
        ctx.stroke();

        ctx.font = "12px " + UI_FONT;
        ctx.fillStyle = C.faint;
        ctx.textBaseline = "alphabetic";
        ctx.textAlign = "left";
        ctx.fillText("2 min ago", left, H - 6);
        ctx.textAlign = "center";
        ctx.fillText("1 min", (left + right) / 2, H - 6);
        ctx.textAlign = "right";
        ctx.fillText("now", right, H - 6);

        if (n < 2) {
            ctx.textAlign = "center";
            ctx.fillStyle = C.muted;
            ctx.font = "14px " + UI_FONT;
            ctx.fillText("Waiting for the first messages", W / 2, (top + bottom) / 2);
            return;
        }

        const points = hist.map((value, i) => ({
            x: right - (n - 1 - i) * step + offset * step,
            y: yOf(value),
        }));
        const revealX = reveal >= 1 ? W + 40 : left + (W - left) * ease;
        const thresholdY = thr !== null && thr !== undefined ? yOf(thr) : null;

        // Reference lines
        ctx.save();
        ctx.globalAlpha = ease;
        ctx.font = "12px " + UI_FONT;
        ctx.textAlign = "left";
        if (base !== null && base !== undefined) {
            const y = Math.round(yOf(base)) + 0.5;
            ctx.setLineDash([6, 6]);
            ctx.strokeStyle = hexA(C.muted, 0.45);
            ctx.beginPath();
            ctx.moveTo(left, y);
            ctx.lineTo(W, y);
            ctx.stroke();
            ctx.setLineDash([]);
            ctx.fillStyle = C.muted;
            ctx.fillText("normal " + fmt(base, 1), left + 2, y - 6);
        }
        if (thresholdY !== null) {
            const y = Math.round(thresholdY) + 0.5;
            ctx.setLineDash([2, 5]);
            ctx.lineCap = "round";
            ctx.strokeStyle = hexA(C.hot, 0.6);
            ctx.beginPath();
            ctx.moveTo(left, y);
            ctx.lineTo(W, y);
            ctx.stroke();
            ctx.setLineDash([]);
            ctx.fillStyle = C.hot;
            ctx.fillText("clip trigger " + fmt(thr, 1), left + 2, y - 6);
        }
        ctx.restore();

        // The line and the area under it. Above the trigger it turns coral.
        const paint = (y0, y1, stroke, fillTop) => {
            ctx.save();
            ctx.beginPath();
            ctx.rect(0, y0, revealX, y1 - y0);
            ctx.clip();

            const gradient = ctx.createLinearGradient(0, top, 0, bottom);
            gradient.addColorStop(0, fillTop);
            gradient.addColorStop(1, hexA(stroke, 0));

            ctx.beginPath();
            curve(points);
            ctx.lineTo(points[n - 1].x, bottom);
            ctx.lineTo(points[0].x, bottom);
            ctx.closePath();
            ctx.fillStyle = gradient;
            ctx.fill();

            ctx.beginPath();
            curve(points);
            ctx.lineWidth = 2.5;
            ctx.lineJoin = "round";
            ctx.lineCap = "round";
            ctx.strokeStyle = stroke;
            ctx.stroke();
            ctx.restore();
        };

        if (thresholdY === null) {
            paint(0, H, C.lav, hexA(C.lav, 0.3));
        } else {
            paint(thresholdY, H, C.lav, hexA(C.lav, 0.3));
            paint(0, thresholdY, C.hot, hexA(C.hot, 0.38));
        }

        // Spike markers
        ctx.save();
        ctx.globalAlpha = ease;
        for (const spike of events) {
            if (spike.ago === null || spike.ago === undefined) continue;
            const x = right - Math.max(0, spike.ago - 1) * step + offset * step;
            if (x < left || x > W - 4) continue;

            ctx.setLineDash([3, 4]);
            ctx.strokeStyle = hexA(C.hot, 0.45);
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(Math.round(x) + 0.5, 26);
            ctx.lineTo(Math.round(x) + 0.5, bottom);
            ctx.stroke();
            ctx.setLineDash([]);

            const label = fmt(spike.multiplier, 1) + "×";
            ctx.font = "700 12px " + DISPLAY_FONT;
            const w = ctx.measureText(label).width + 16;
            const px = Math.min(Math.max(x - w / 2, 4), W - w - 4);
            roundRect(px, 5, w, 21, 10.5);
            ctx.fillStyle = C.raise;
            ctx.fill();
            ctx.strokeStyle = C.hot;
            ctx.stroke();
            ctx.fillStyle = C.hot;
            ctx.textAlign = "center";
            ctx.fillText(label, px + w / 2, 20);
        }

        // Where clips were taken
        for (const clip of clips) {
            if (clip.status === "failed" || clip.ago === null || clip.ago === undefined) continue;
            const x = right - Math.max(0, clip.ago - 1) * step + offset * step;
            if (x < left || x > W - 4) continue;
            const color = clip.status === "ready" ? C.ok : C.amber;
            ctx.beginPath();
            ctx.arc(x, bottom, 6, 0, Math.PI * 2);
            ctx.fillStyle = hexA(color, 0.22);
            ctx.fill();
            ctx.beginPath();
            ctx.arc(x, bottom, 3, 0, Math.PI * 2);
            ctx.fillStyle = color;
            ctx.fill();
        }
        ctx.restore();

        // Hover read-out
        let showTip = false;
        if (hoverX !== null) {
            const i = Math.round(n - 1 - (right + offset * step - hoverX) / step);
            if (i >= 0 && i < n) {
                const p = points[i];
                ctx.strokeStyle = hexA(C.text, 0.25);
                ctx.lineWidth = 1;
                ctx.beginPath();
                ctx.moveTo(Math.round(p.x) + 0.5, top - 4);
                ctx.lineTo(Math.round(p.x) + 0.5, bottom);
                ctx.stroke();

                const hot = thr !== null && thr !== undefined && hist[i] >= thr;
                ctx.beginPath();
                ctx.arc(p.x, p.y, 4.5, 0, Math.PI * 2);
                ctx.fillStyle = hot ? C.hot : C.lav;
                ctx.fill();
                ctx.lineWidth = 2;
                ctx.strokeStyle = token("--ink");
                ctx.stroke();

                const age = n - 1 - i;
                tipValue.textContent = hist[i];
                tipText.textContent = age === 0 ? "msg/s, latest" : "msg/s, " + age + "s ago";
                const width = tip.offsetWidth;
                const height = tip.offsetHeight;
                tip.style.left = Math.min(Math.max(p.x - width / 2, 4), W - width - 4) + "px";
                tip.style.top = Math.max(30, p.y - height - 14) + "px";
                showTip = true;
            }
        }
        tip.classList.toggle("show", showTip);
    }

    function frame(now) {
        const dt = Math.min(64, now - last);
        last = now;

        if (offset > 0) {
            offset = reduced() ? 0 : Math.max(0, offset - (dt / 1000) * Math.max(1, offset * 1.2));
        }
        if (reveal < 1) {
            reveal = reduced() ? 1 : Math.min(1, reveal + dt / 1300);
        }

        let peak = 0;
        for (const value of hist) if (value > peak) peak = value;
        const target = Math.max(6, peak * 1.18, thr ? thr * 1.3 : 0);
        yMax += (target - yMax) * (reduced() ? 1 : 1 - Math.exp(-dt / 220));

        draw();
        requestAnimationFrame(frame);
    }

    function start() {
        resize();
        new ResizeObserver(resize).observe(wrap);
        wrap.addEventListener("pointermove", event => {
            hoverX = event.clientX - wrap.getBoundingClientRect().left;
        });
        wrap.addEventListener("pointerleave", () => { hoverX = null; });
        requestAnimationFrame(frame);
    }

    return { start: start, update: update };
})();

// ------------------------------------------------------------------- 7TV

let sevenTVEmotes = new Map();
let emotesVersion = null;
let emoteRequestInFlight = false;
let emoteRetryAt = 0;
let latestChat = [];

function setEmoteChip(label, kind, title) {
    const chip = $("emoteChip");
    chip.className = "chip " + kind;
    chip.textContent = label;
    chip.title = title || "";
}

async function loadSevenTVEmotes() {
    if (emoteRequestInFlight) return;
    emoteRequestInFlight = true;

    try {
        const response = await fetch(BACKEND + "/api/emotes", { cache: "no-store" });
        if (!response.ok) throw new Error("HTTP " + response.status);

        const data = await response.json();

        // A Map avoids clashes with built-in property names like "constructor".
        sevenTVEmotes = new Map(Object.entries(data.emotes || {}));
        emotesVersion = data.version;
        emoteRetryAt = 0;

        const counts = data.counts || { global: 0, channel: 0 };
        const note = $("emoteNote");
        note.hidden = true;

        if (data.status === "ready") {
            setEmoteChip(
                "7TV " + fmt(sevenTVEmotes.size),
                "ok",
                data.channel_found
                    ? counts.channel + " channel emotes and " + counts.global + " global emotes"
                    : "No 7TV emote set for this channel. Showing " + counts.global + " global emotes."
            );
        } else if (data.status === "waiting_for_channel") {
            setEmoteChip("7TV global", "live", "Waiting for chat to identify the channel's emotes");
        } else if (data.status === "error") {
            setEmoteChip("7TV offline", "warn", "The server couldn't reach 7TV");
            note.textContent = "7TV emotes couldn't be loaded. Trying again in a moment.";
            note.hidden = false;
        } else {
            setEmoteChip("7TV loading", "live", "");
        }

        // Re-draw existing messages so they pick up the emotes.
        renderChat(latestChat, true);
    } catch (error) {
        console.error("7TV loading error:", error);
        setEmoteChip("7TV offline", "warn", "Couldn't load emotes");
        emoteRetryAt = Date.now() + 10000;
    } finally {
        emoteRequestInFlight = false;
    }
}

// The backend bumps emotes_version whenever its emote list changes.
function syncEmotes(version) {
    if (version === undefined || version === emotesVersion) return;
    if (emoteRequestInFlight || Date.now() < emoteRetryAt) return;
    loadSevenTVEmotes();
}

function makeEmoteImage(name, emote, isOverlay) {
    const img = document.createElement("img");
    img.className = "seventv-emote" + (isOverlay ? " zw" : "");
    img.src = emote.url;
    img.alt = name;
    img.title = name + (emote.scope === "channel" ? " (channel 7TV emote)" : " (global 7TV emote)");
    img.decoding = "async";
    img.draggable = false;
    img.onerror = () => {
        if (isOverlay) img.remove();
        else img.replaceWith(document.createTextNode(name));
    };
    return img;
}

// ------------------------------------------------------------------- chat

const CHAT_LIMIT = 100; // matches the backend's chat buffer
const chatEl = $("chat");
const jump = $("jump");
let lastChatId = null;
let unseen = 0;

function usernameColor(username) {
    const colors = ["#ff7b9c", "#a78bfa", "#60a5fa", "#34d399", "#fbbf24", "#fb923c", "#e879f9", "#2dd4bf"];
    let hash = 0;
    for (let i = 0; i < username.length; i++) {
        hash = username.charCodeAt(i) + ((hash << 5) - hash);
    }
    return colors[Math.abs(hash) % colors.length];
}

function renderMessageContent(container, message) {
    // Twitch splits chat into words on whitespace, and emote names must match
    // a whole word exactly (case-sensitive).
    let lastEmote = null;

    for (const word of message.split(/(\s+)/)) {
        if (!word) continue;

        if (/^\s+$/.test(word)) {
            container.appendChild(document.createTextNode(word));
            continue;
        }

        const emote = sevenTVEmotes.get(word);

        if (!emote || !emote.url) {
            container.appendChild(document.createTextNode(word));
            lastEmote = null;
            continue;
        }

        // Zero-width emotes are drawn on top of the emote before them.
        if (emote.zero_width && lastEmote && lastEmote.parentElement) {
            let stack = lastEmote.parentElement;

            if (!stack.classList.contains("emote-stack")) {
                stack = document.createElement("span");
                stack.className = "emote-stack";
                lastEmote.replaceWith(stack);
                stack.appendChild(lastEmote);
            }

            stack.appendChild(makeEmoteImage(word, emote, true));
            continue;
        }

        const img = makeEmoteImage(word, emote, false);
        container.appendChild(img);
        lastEmote = img;
    }
}

function buildChatRow(message) {
    const row = el("div", "chat-message");

    const user = el("span", "chat-user", message.display_name || message.username || "Unknown");
    user.style.color = usernameColor(message.username || "");

    const content = el("span", "chat-content");
    renderMessageContent(content, String(message.text || "").replace(/\r?\n/g, ""));

    row.append(user, content);
    return row;
}

function chatEmpty() {
    const box = el("div", "empty");
    box.append(el("strong", "", "Waiting for messages"), document.createTextNode("Chat shows up here as soon as someone talks."));
    return box;
}

function atBottom() {
    return chatEl.scrollHeight - chatEl.scrollTop - chatEl.clientHeight < 80;
}

function updateJump() {
    if (unseen <= 0) {
        jump.hidden = true;
        return;
    }
    jump.replaceChildren(
        document.createTextNode(unseen + (unseen === 1 ? " new message" : " new messages")),
    );
    jump.insertAdjacentHTML("beforeend", ICON.down);
    jump.hidden = false;
}

chatEl.addEventListener("scroll", () => {
    if (atBottom() && unseen) {
        unseen = 0;
        updateJump();
    }
}, { passive: true });

jump.addEventListener("click", () => {
    chatEl.scrollTo({ top: chatEl.scrollHeight, behavior: reduced() ? "instant" : "smooth" });
});

// New messages are appended instead of rebuilding the list, so animated emotes
// already on screen keep playing. A full rebuild only happens when the emote
// list changes.
function renderChat(messages, forceRebuild) {
    latestChat = messages || [];
    const items = latestChat;

    if (!items.length) {
        if (lastChatId !== null || !chatEl.firstElementChild) {
            chatEl.replaceChildren(chatEmpty());
        }
        lastChatId = null;
        return;
    }

    const first = lastChatId === null;
    const newest = items[items.length - 1].id;
    // A lower ID than before means the server restarted.
    const rebuild = forceRebuild || first || newest < lastChatId;

    if (!rebuild && newest === lastChatId) return;

    const stick = atBottom();
    const previousTop = chatEl.scrollTop;
    let added = 0;

    if (rebuild) {
        chatEl.replaceChildren();
        for (const message of items) chatEl.appendChild(buildChatRow(message));
    } else {
        const fresh = items.filter(message => message.id > lastChatId);
        fresh.forEach((message, index) => {
            const row = buildChatRow(message);
            if (fresh.length - index <= 12) row.classList.add("enter");
            chatEl.appendChild(row);
        });
        added = fresh.length;

        while (chatEl.children.length > CHAT_LIMIT) chatEl.firstElementChild.remove();
    }

    lastChatId = newest;

    // Follow new messages, unless the person has scrolled up to read.
    if (first || stick) {
        chatEl.scrollTo({ top: chatEl.scrollHeight, behavior: "instant" });
        unseen = 0;
    } else {
        chatEl.scrollTop = previousTop;
        unseen += added;
    }
    updateJump();
}

// One message pushed by the server the moment it was sent.
function addChatMessage(message) {
    if (!message) return;
    const last = latestChat.length ? latestChat[latestChat.length - 1].id : 0;
    if (message.id <= last) return; // already shown
    renderChat(latestChat.concat(message).slice(-CHAT_LIMIT), false);
}

// -------------------------------------------------------------- clip list

const clipRows = new Map();
const CLIP_STATUS = {
    waiting: ["Waiting", "live"],
    creating: ["Starting", "live"],
    processing: ["Saving", "live"],
    ready: ["Ready", "ok"],
    failed: ["Failed", "err"],
};

function reconcile(list, nodes) {
    nodes.forEach((node, index) => {
        if (list.children[index] !== node) list.insertBefore(node, list.children[index] || null);
    });
    while (list.children.length > nodes.length) list.lastElementChild.remove();
}

function buildClipRow(delay) {
    const row = el("li", "clip enter");
    row.style.setProperty("--d", delay + "ms");

    const main = el("div", "clip-main");
    const meta = el("div", "clip-meta");
    meta.append(el("span", "src"), el("span", "when"));
    const problem = el("div", "clip-error");
    problem.hidden = true;
    main.append(el("div", "clip-title"), meta, problem);

    const side = el("div", "clip-side");
    side.append(el("span", "chip"), el("div", "links"));

    row.append(el("div", "thumb"), main, side);
    return row;
}

function paintThumb(row, clip) {
    const key = clip.status + "|" + (clip.thumbnail_url || "");
    if (row._thumb === key) return;
    row._thumb = key;

    const thumb = row.querySelector(".thumb");
    thumb.replaceChildren();

    if (clip.status === "waiting" || clip.status === "creating" || clip.status === "processing") {
        thumb.appendChild(el("span", "spin"));
    } else if (clip.status === "failed") {
        thumb.innerHTML = ICON.warn;
    } else {
        thumb.innerHTML = ICON.film;
        const url = safeUrl(clip.thumbnail_url);
        if (url) {
            const img = new Image();
            img.alt = "";
            img.decoding = "async";
            img.referrerPolicy = "no-referrer";
            img.onload = () => img.classList.add("loaded");
            img.onerror = () => img.remove();
            img.src = url;
            thumb.appendChild(img);
        }
    }
}

function paintLinks(row, clip) {
    const key = clip.status + "|" + (clip.url || "") + "|" + (clip.edit_url || "");
    if (row._links === key) return;
    row._links = key;

    const links = row.querySelector(".links");
    links.replaceChildren();
    if (clip.status !== "ready") return;

    const save = el("button", "linkbtn", "Save");
    save.type = "button";
    save.title = "Download the video file";
    save.addEventListener("click", () => saveClip(clip, save));
    links.appendChild(save);

    for (const [label, url] of [["Watch", clip.url], ["Edit", clip.edit_url]]) {
        const href = safeUrl(url);
        if (!href) continue;
        const a = el("a", "", label);
        a.href = href;
        a.target = "_blank";
        a.rel = "noopener";
        links.appendChild(a);
    }
}

function paintClip(row, clip) {
    row.dataset.status = clip.status;

    const title = row.querySelector(".clip-title");
    title.textContent = clip.title;
    title.title = clip.title;

    let source = clip.source === "auto" ? "Auto" : "Manual";
    if (clip.source === "auto" && clip.multiplier) source += ", " + fmt(clip.multiplier, 1) + "×";
    row.querySelector(".src").textContent = source;
    row.querySelector(".when").textContent = clip.status === "waiting"
        ? (clip.due_in ? "Clipping in " + clip.due_in + "s" : "Clipping now")
        : agoText(ageOf(clip));

    const problem = row.querySelector(".clip-error");
    if (clip.error) {
        if (problem.textContent !== clip.error) problem.textContent = clip.error;
        problem.hidden = false;
    } else {
        problem.hidden = true;
    }

    const chip = row.querySelector(".chip");
    const [label, kind] = CLIP_STATUS[clip.status] || ["", ""];
    if (row._status !== clip.status) {
        const changed = row._status !== undefined;
        row._status = clip.status;
        chip.className = "chip " + kind;
        chip.textContent = label;
        if (changed) restartAnimation(chip, "pop");
    }

    paintThumb(row, clip);
    paintLinks(row, clip);
}

function peakText(clipping) {
    const seconds = clipping && clipping.delay_after_peak;
    return seconds ? " " + Math.round(seconds) + " seconds after the peak" : "";
}

async function saveClip(clip, button) {
    if (button.disabled) return;
    button.disabled = true;
    const label = button.textContent;
    button.textContent = "Saving";

    try {
        const result = await api("/api/clips/" + encodeURIComponent(clip.uid) + "/download", {}, true);
        if (!result.ok) {
            apiFailed(result);
            return;
        }
        const blob = await result.response.blob();
        const disposition = result.response.headers.get("Content-Disposition") || "";
        const match = /filename="([^"]+)"/.exec(disposition);

        const href = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = href;
        link.download = match ? match[1] : (clip.clip_id || "clip") + ".mp4";
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(href), 60000);
        toast("Clip saved to your downloads.", "ok");
    } catch (error) {
        toast("The download didn't finish. Try again.", "err");
    } finally {
        button.disabled = false;
        button.textContent = label;
    }
}

function emptyBox(title, body, action) {
    const box = el("li", "empty");
    box.append(el("strong", "", title), el("span", "empty-body", body));
    if (action) {
        const button = el("button", "btn small", action.label);
        button.type = "button";
        button.addEventListener("click", action.run);
        box.appendChild(button);
    }
    return box;
}

let clipsEmptyKey = null;

function renderClips(list, clipping) {
    const holder = $("clips");

    if (!list.length) {
        for (const row of clipRows.values()) row.remove();
        clipRows.clear();

        const ready = clipping && clipping.ready;
        const key = ready ? "ready" : "setup:" + (clipping ? clipping.reason : "");
        if (clipsEmptyKey !== key || !holder.firstElementChild) {
            clipsEmptyKey = key;
            holder.replaceChildren(
                ready
                    ? emptyBox("No clips yet", "Press Clip now, or turn on auto-clip to save one" + peakText(clipping) + " every time chat spikes.")
                    : emptyBox(
                        "Clipping isn't set up yet",
                        (clipping && clipping.reason) || "Connect your Twitch account to start clipping.",
                        { label: "Set up clipping", run: openSetup }
                    )
            );
        }
        return;
    }

    if (clipsEmptyKey !== null) {
        clipsEmptyKey = null;
        holder.replaceChildren();
    }

    const present = new Set(list.map(clip => clip.uid));
    for (const [uid, row] of clipRows) {
        if (!present.has(uid)) {
            row.remove();
            clipRows.delete(uid);
        }
    }

    const nodes = list.map((clip, index) => {
        let row = clipRows.get(clip.uid);
        if (!row) {
            row = buildClipRow(S.first ? Math.min(index, 6) * 70 : 0);
            clipRows.set(clip.uid, row);
        }
        paintClip(row, clip);
        return row;
    });
    reconcile(holder, nodes);
}

// ------------------------------------------------------------- highlights

const spikeRows = new Map();
let spikesEmptyKey = null;

function buildSpikeRow(delay) {
    const row = el("li", "spike enter");
    row.style.setProperty("--d", delay + "ms");
    const mult = el("div", "mult");
    const body = el("div");
    body.append(el("div", "spike-line"), el("div", "spike-meta"));
    row.append(mult, body, el("span", "chip"));
    return row;
}

function paintSpike(row, spike, clips) {
    const mult = row.querySelector(".mult");
    mult.textContent = fmt(spike.multiplier, 1);
    mult.appendChild(el("small", "", "×"));

    row.querySelector(".spike-line").textContent = "Chat hit " + fmt(spike.rate, 1) + " messages per second";
    row.querySelector(".spike-meta").textContent =
        agoText(ageOf(spike)) + ", normal was " + fmt(spike.baseline, 1);

    const chip = row.querySelector(".chip");
    const clip = clips.find(item => item.event_id === spike.id);
    if (!clip) {
        chip.hidden = true;
        row._clip = undefined;
        return;
    }
    chip.hidden = false;
    if (row._clip !== clip.status) {
        const changed = row._clip !== undefined;
        row._clip = clip.status;
        const label = { waiting: "Queued", creating: "Clipping", processing: "Clipping", ready: "Clipped", failed: "Clip failed" }[clip.status];
        chip.className = "chip " + (CLIP_STATUS[clip.status] || ["", ""])[1];
        chip.textContent = label;
        if (changed) restartAnimation(chip, "pop");
    }
}

function renderSpikes(list, clips, phase) {
    const holder = $("events");
    $("spikeCount").textContent = list.length ? list.length + (list.length === 1 ? " spike" : " spikes") : "";

    if (!list.length) {
        for (const row of spikeRows.values()) row.remove();
        spikeRows.clear();
        const key = phase;
        if (spikesEmptyKey !== key || !holder.firstElementChild) {
            spikesEmptyKey = key;
            holder.replaceChildren(
                phase === "calibrating"
                    ? emptyBox("Still learning", "Spike detection starts once AutoClip knows how busy chat normally is.")
                    : emptyBox("No spikes yet", "A highlight shows up here when chat gets much busier than usual.")
            );
        }
        return;
    }

    if (spikesEmptyKey !== null) {
        spikesEmptyKey = null;
        holder.replaceChildren();
    }

    const present = new Set(list.map(spike => spike.id));
    for (const [id, row] of spikeRows) {
        if (!present.has(id)) {
            row.remove();
            spikeRows.delete(id);
        }
    }

    const nodes = list.map((spike, index) => {
        let row = spikeRows.get(spike.id);
        if (!row) {
            row = buildSpikeRow(S.first ? Math.min(index, 6) * 70 : 0);
            spikeRows.set(spike.id, row);
        }
        paintSpike(row, spike, clips);
        return row;
    });
    reconcile(holder, nodes);
}

// ----------------------------------------------------------- clip controls

function openSetup() {
    renderSetup();
    openSheet($("setup"));
}

function renderControls(s) {
    const c = s.clipping || {};
    const button = $("clipBtn");
    const label = $("clipBtnText");

    if (!S.clipBusy) {
        if (c.ready) {
            button.className = "btn rec";
            label.textContent = "Clip now";
        } else if (c.needs_reconnect) {
            button.className = "btn";
            label.textContent = "Reconnect Twitch";
        } else {
            button.className = "btn";
            label.textContent = "Set up clipping";
        }
    }

    if (!S.autoBusy) $("auto").checked = !!s.auto_clip_enabled;

    const chip = $("clipChip");
    if (c.ready) {
        chip.className = "chip ok";
        chip.textContent = c.account ? "Connected as " + c.account : "Connected";
    } else if (c.needs_reconnect) {
        chip.className = "chip err";
        chip.textContent = "Reconnect needed";
    } else {
        chip.className = "chip warn";
        chip.textContent = "Setup needed";
    }

    const note = $("clipNote");
    const skipped = c.last_error && c.last_error.indexOf("Auto-clip skipped") === 0;
    note.hidden = !skipped;
    if (skipped && note.textContent !== c.last_error) note.textContent = c.last_error;
}

async function clipNow() {
    const c = S.snap && S.snap.clipping;
    if (!c || !c.ready) {
        openSetup();
        return;
    }
    if (S.clipBusy) return;

    const button = $("clipBtn");
    const label = $("clipBtnText");
    S.clipBusy = true;
    button.disabled = true;
    button.classList.add("firing");
    label.textContent = "Clipping";

    const result = await api("/api/clip", {});

    S.clipBusy = false;
    button.disabled = false;
    button.classList.remove("firing");
    label.textContent = "Clip now";

    if (!result.ok) {
        apiFailed(result);
        return;
    }

    toast("Clip started. It shows up in the list when Twitch has saved it.", "ok");
    if (result.clip && S.snap) {
        S.clipStatus.set(result.clip.uid, result.clip.status);
        S.snap.clips = [result.clip].concat((S.snap.clips || []).filter(item => item.uid !== result.clip.uid));
        renderClips(S.snap.clips, S.snap.clipping);
    }
}

$("clipBtn").addEventListener("click", clipNow);
$("setupBtn").addEventListener("click", openSetup);
$("clipChip").addEventListener("click", openSetup);

$("auto").addEventListener("click", event => {
    const c = S.snap && S.snap.clipping;
    if (!c || !c.ready) {
        event.preventDefault();
        openSetup();
    }
});

$("auto").addEventListener("change", async event => {
    const box = event.target;
    const wanted = box.checked;
    S.autoBusy = true;
    const result = await api("/api/auto-clip", { enabled: wanted });
    S.autoBusy = false;

    if (!result.ok) {
        box.checked = !wanted;
        apiFailed(result);
        return;
    }
    if (S.snap) S.snap.auto_clip_enabled = wanted;
    toast(
        wanted ? "Auto-clip is on. A clip is saved" + peakText(S.snap && S.snap.clipping) + " every time chat spikes." : "Auto-clip is off.",
        wanted ? "ok" : ""
    );
});

// ------------------------------------------------------------ setup sheet

function markStep(id, done, number) {
    const step = $(id);
    step.classList.toggle("done", done);
    const mark = step.querySelector(".step-mark");
    if (done) mark.innerHTML = ICON.check;
    else mark.textContent = number;
}

function renderSetup() {
    const s = S.snap;
    const c = (s && s.clipping) || {};

    const uri = c.redirect_uri || (location.origin + "/auth/twitch/callback");
    $("redirect").textContent = uri;

    const banner = $("setupBanner");
    banner.classList.toggle("ok", !!c.ready);
    banner.textContent = c.ready
        ? "Clipping is ready" + (c.account ? ". Clips are made through " + c.account + "." : ".")
        : (c.reason || "Finish these steps to turn on clipping.");

    const missing = [];
    if (!c.client_configured) missing.push("TWITCH_CLIENT_ID");
    if (!c.secret_configured) missing.push("TWITCH_CLIENT_SECRET");
    if (!c.admin_configured && !IS_LOCAL) missing.push("ADMIN_KEY");

    markStep("st1", !!c.client_configured, "1");
    markStep("st2", missing.length === 0, "2");
    markStep("st3", !!c.connected && !c.needs_reconnect, "3");
    markStep("st4", false, "4");

    const gap = $("missing");
    gap.hidden = missing.length === 0;
    gap.textContent = missing.length ? "Still missing: " + missing.join(", ") + "." : "";

    const connect = $("connectBtn");
    const disconnect = $("disconnectBtn");
    const text = $("connectText");

    if (c.connected && !c.needs_reconnect) {
        text.textContent = "Connected" + (c.account ? " as " + c.account : "") + ". Clips are made through this account." +
            (c.can_download ? "" : " To save clips as video files, connect again and approve the extra permission.");
        connect.textContent = "Connect a different account";
        connect.className = "btn";
        disconnect.hidden = false;
    } else if (c.needs_reconnect) {
        text.textContent = c.reason || "Twitch needs you to sign in again.";
        connect.textContent = "Reconnect Twitch";
        connect.className = "btn primary";
        disconnect.hidden = false;
    } else {
        text.textContent = "You'll go to Twitch to allow clip creation and saving, then come straight back here. The account you pick makes the clips.";
        connect.textContent = "Connect Twitch";
        connect.className = "btn primary";
        disconnect.hidden = true;
    }

    $("saveNote").textContent =
        "Saving clips as video files is a Twitch rule: it only works if the account is " +
        ((s && s.channel) || "the channel owner") + " or one of their channel editors.";

    $("st4").hidden = !(c.connected && !c.needs_reconnect);
}

$("setupClose").addEventListener("click", () => closeSheet($("setup")));

$("copyRedirect").addEventListener("click", async event => {
    const ok = await copyText($("redirect").textContent);
    flash(event.currentTarget, ok ? "Copied" : "Press Ctrl+C");
});

$("connectBtn").addEventListener("click", async event => {
    const button = event.currentTarget;
    button.disabled = true;
    const result = await api("/api/auth/twitch/start", {});
    if (result.ok && typeof result.url === "string" && result.url.indexOf("https://id.twitch.tv/") === 0) {
        location.href = result.url;
        return;
    }
    button.disabled = false;
    apiFailed(result);
});

$("disconnectBtn").addEventListener("click", async event => {
    const button = event.currentTarget;
    if (!button.dataset.armed) {
        button.dataset.armed = "1";
        button.textContent = "Click again to confirm";
        setTimeout(() => {
            delete button.dataset.armed;
            button.textContent = "Disconnect";
        }, 3500);
        return;
    }
    delete button.dataset.armed;
    button.textContent = "Disconnect";

    const result = await api("/api/auth/twitch/disconnect", {});
    if (result.ok) toast("Twitch disconnected. Auto-clip is off.");
    else apiFailed(result);
});

$("copyToken").addEventListener("click", async event => {
    const button = event.currentTarget;
    const result = await api("/api/auth/twitch/refresh-token", {});
    if (!result.ok) {
        apiFailed(result);
        return;
    }
    const ok = await copyText(result.refresh_token);
    if (ok) {
        flash(button, "Copied");
        toast("Refresh token copied. Save it as TWITCH_CLIP_REFRESH_TOKEN in your host's settings.", "ok");
    } else {
        toast("Your browser blocked copying. Allow clipboard access and try again.", "err");
    }
});

// ---------------------------------------------------------- the dashboard

function setStatus() {
    const s = S.snap;
    const fresh = S.lastAt && performance.now() - S.lastAt < 4500;
    const box = $("status");
    let state = "wait";
    let text = "Connecting";
    let title = "";

    if (!fresh) {
        if (s) {
            state = "down";
            text = "Reconnecting";
        }
    } else if (s.connection === "connected") {
        state = "live";
        text = "Live chat";
    } else if (s.connection === "error") {
        state = "down";
        text = "Chat offline";
        title = s.error || "";
    } else {
        text = "Joining chat";
    }

    box.dataset.state = state;
    box.title = title;
    if ($("statusText").textContent !== text) $("statusText").textContent = text;
    $("app").classList.toggle("stale", !fresh && !!s);
}

function verdictFor(s, hot) {
    if (s.connection === "error") return "Can't reach Twitch chat. Trying again.";
    if (s.connection !== "connected") return "Joining chat";
    if (s.phase === "calibrating") {
        const left = Math.max(0, s.calibration_total - s.calibration_elapsed);
        return "Chat is live. Spike detection starts in " + left + "s.";
    }
    if (hot) return "Chat is " + fmt(s.multiplier, 1) + "× busier than normal";
    const base = s.baseline || 0;
    if (base > 0 && s.messages_per_second >= base * 1.4) return "Chat is picking up";
    if (s.messages_per_second < 0.2) return "Chat is quiet";
    return "Chat is at a normal pace";
}

function announceSpike(spike) {
    restartAnimation($("hero"), "fire");
    const auto = S.snap && S.snap.auto_clip_enabled;
    toast(
        "Chat spiked to " + fmt(spike.multiplier, 1) + "× normal" + (auto ? ". Auto-clip will save it" + peakText(S.snap && S.snap.clipping) + "." : "."),
        "warn"
    );
}

function watchClips(clips) {
    for (const clip of clips) {
        const before = S.clipStatus.get(clip.uid);
        S.clipStatus.set(clip.uid, clip.status);
        if (S.first || before === clip.status) continue;

        if (clip.status === "ready") {
            toast("Clip ready: " + clip.title, "ok", { link: clip.url, linkText: "Watch" });
        } else if (clip.status === "failed") {
            toast(clip.error || "The clip couldn't be saved.", "err");
        }
    }
}

function render(s) {
    const previous = S.snap;
    S.snap = s;
    S.lastAt = performance.now();

    document.body.classList.remove("loading");

    const channel = s.channel || "";
    if ($("channel").textContent !== channel) $("channel").textContent = channel;

    const monitoring = s.phase === "monitoring";
    const hot = monitoring && s.threshold !== null && s.recent_rate >= s.threshold;
    document.body.classList.toggle("hot", hot);
    document.title = (hot ? "Spike! " : "") + channel + " | AutoClip";

    tween($("rate"), s.messages_per_second, 1, 900);

    const verdict = $("verdict");
    const line = verdictFor(s, hot);
    if (verdict.textContent !== line) {
        verdict.textContent = line;
        restartAnimation(verdict, "swap");
    }

    // Spike meter: the red tick sits at the clip trigger.
    const meter = $("meter");
    meter.classList.toggle("off", !monitoring);
    if (monitoring && s.threshold) {
        $("heatFill").style.width = Math.min(100, s.recent_rate / (s.threshold * 1.2) * 100) + "%";
        $("meterText").textContent = fmt(s.recent_rate, 1) + " of " + fmt(s.threshold, 1);
    } else {
        $("heatFill").style.width = "0%";
        $("meterText").textContent = "—";
    }

    const calibrate = $("calibrate");
    calibrate.classList.toggle("done", monitoring);
    if (!monitoring) {
        $("calFill").style.width = s.calibration_progress + "%";
        $("calLeft").textContent = Math.max(0, s.calibration_total - s.calibration_elapsed) + "s left";
    }

    tween($("sBase"), s.baseline, 1);
    tween($("sThr"), s.threshold, 1);
    tween($("sMsgs"), s.total_messages, 0, 600);
    tween($("sChat"), s.unique_chatters, 0, 600);
    tween($("sClips"), (s.clips || []).filter(clip => clip.status === "ready").length, 0, 600);

    trace.update(s);

    // Announce a new spike once, never the ones that were already there.
    const newest = (s.events || [])[0];
    const newestId = newest ? newest.id : 0;
    if (S.seenEvent === null) {
        S.seenEvent = newestId;
    } else {
        if (newest && newestId > S.seenEvent) announceSpike(newest);
        S.seenEvent = newestId;
    }

    // Chat normally arrives on its own as it is sent; a full list only comes
    // with the first load or a poll.
    if (s.chat_messages) renderChat(s.chat_messages, false);
    syncEmotes(s.emotes_version);

    watchClips(s.clips || []);
    renderClips(s.clips || [], s.clipping);
    renderSpikes(s.events || [], s.clips || [], s.phase);
    renderControls(s);
    if ($("setup").open) renderSetup();
    setStatus();

    S.first = false;
    void previous;
}

// -------------------------------------------------------------- connection

let socket = null;
let retries = 0;
let retryTimer = null;
let pollTimer = null;

async function poll() {
    try {
        const response = await fetch(BACKEND + "/api/status", { cache: "no-store" });
        if (response.ok) render(await response.json());
    } catch (error) { /* the next poll tries again */ }
}

function startPolling() {
    if (!pollTimer) pollTimer = setInterval(poll, 2000);
}

function stopPolling() {
    clearInterval(pollTimer);
    pollTimer = null;
}

function scheduleRetry() {
    if (retryTimer) return;
    retries = Math.min(retries + 1, 5);
    retryTimer = setTimeout(() => {
        retryTimer = null;
        connect();
    }, [0, 1000, 2000, 3000, 5000, 5000][retries]);
}

function connect() {
    if (socket && socket.readyState <= 1) return;

    try {
        socket = new WebSocket(BACKEND.replace(/^http/, "ws") + "/ws");
    } catch (error) {
        startPolling();
        scheduleRetry();
        return;
    }

    socket.onopen = () => {
        S.wsOpen = true;
        retries = 0;
        stopPolling();
    };
    socket.onmessage = event => {
        try {
            const data = JSON.parse(event.data);
            if (data.type === "chat") addChatMessage(data.message);
            else render(data);
        } catch (error) {
            console.error("Dashboard data error:", error);
        }
    };
    socket.onclose = () => {
        S.wsOpen = false;
        socket = null;
        setStatus();
        startPolling();
        scheduleRetry();
    };
    socket.onerror = () => { /* onclose follows */ };
}

document.addEventListener("visibilitychange", () => {
    if (!document.hidden && !socket) connect();
});

setInterval(setStatus, 1000);


// --------------------------------------------------------- page navigation
const menuToggle = $("menuToggle");
const navDrawer = $("navDrawer");
const dashboardPage = $("dashboardPage");
const contributionPage = $("contributionPage");
let leaderboardLoadedAt = 0;

function setPage(page) {
    const contribution = page === "contribution";
    dashboardPage.hidden = contribution;
    contributionPage.hidden = !contribution;
    document.querySelectorAll(".nav-item").forEach(button => {
        button.classList.toggle("active", button.dataset.page === page);
    });
    navDrawer.hidden = true;
    menuToggle.setAttribute("aria-expanded", "false");
    menuToggle.setAttribute("aria-label", "Open navigation");
    if (contribution && Date.now() - leaderboardLoadedAt > 55000) loadLeaderboard();
}

menuToggle.addEventListener("click", () => {
    navDrawer.hidden = !navDrawer.hidden;
    menuToggle.setAttribute("aria-expanded", String(!navDrawer.hidden));
    menuToggle.setAttribute("aria-label", navDrawer.hidden ? "Open navigation" : "Close navigation");
});
document.querySelectorAll(".nav-item").forEach(button => button.addEventListener("click", () => setPage(button.dataset.page)));
document.addEventListener("click", event => {
    if (!navDrawer.hidden && !navDrawer.contains(event.target) && !menuToggle.contains(event.target)) {
        navDrawer.hidden = true;
        menuToggle.setAttribute("aria-expanded", "false");
    }
});

function loadLeaderboard() {
    const body = $("leaderboardRows");
    body.replaceChildren();
    const loading = document.createElement("tr");
    const loadingCell = document.createElement("td");
    loadingCell.colSpan = 6;
    loadingCell.className = "table-message";
    loadingCell.textContent = "Loading the most-watched live streams…";
    loading.appendChild(loadingCell);
    body.appendChild(loading);
    $("lbUpdated").textContent = "Updating";

    fetch(BACKEND + "/api/leaderboard", { cache: "no-store" }).then(async response => {
        const data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || "Could not load leaderboard.");
        body.replaceChildren();
        const streams = data.streams || [];
        let viewerTotal = 0;
        streams.forEach((stream, index) => {
            viewerTotal += Number(stream.viewers || 0);
            const tr = document.createElement("tr");
            const rank = document.createElement("td");
            rank.className = "streamer-rank";
            rank.textContent = String(index + 1).padStart(2, "0");
            tr.appendChild(rank);

            const nameCell = document.createElement("td");
            const identity = document.createElement("div");
            identity.className = "streamer-cell";
            if (stream.thumbnail) {
                const avatar = document.createElement("img");
                avatar.className = "streamer-avatar";
                avatar.src = stream.thumbnail;
                avatar.alt = "";
                avatar.loading = "lazy";
                identity.appendChild(avatar);
            }
            const link = document.createElement("a");
            link.className = "streamer-name";
            link.href = stream.url;
            link.target = "_blank";
            link.rel = "noopener";
            link.textContent = stream.name || stream.login || "Unknown streamer";
            identity.appendChild(link);
            nameCell.appendChild(identity);
            tr.appendChild(nameCell);

            const category = document.createElement("td");
            category.textContent = stream.game || "—";
            tr.appendChild(category);
            const viewers = document.createElement("td");
            viewers.className = "numeric";
            viewers.textContent = fmt(stream.viewers, 0);
            tr.appendChild(viewers);
            const chatters = document.createElement("td");
            chatters.className = "numeric contribution-pending";
            chatters.textContent = "Not measured";
            tr.appendChild(chatters);
            const contribution = document.createElement("td");
            contribution.className = "numeric contribution-pending";
            contribution.textContent = "—";
            tr.appendChild(contribution);
            body.appendChild(tr);
        });
        if (!streams.length) {
            const tr = document.createElement("tr");
            const td = document.createElement("td");
            td.colSpan = 6;
            td.className = "table-message";
            td.textContent = "No live streams were returned by Twitch.";
            tr.appendChild(td);
            body.appendChild(tr);
        }
        $("lbStreamCount").textContent = fmt(streams.length);
        $("lbViewerTotal").textContent = fmt(viewerTotal);
        $("lbAverage").textContent = "—";
        $("lbUpdated").textContent = "Updated " + new Date(data.updated_at).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
        $("lbFootnote").textContent = "The live top-100 list is working. Chat contribution is intentionally left blank for now: Twitch does not provide a public, universal count of unique active chatters for every channel. AutoClip needs a separate chat-collection system before it can calculate a trustworthy five-minute participation rate.";
        leaderboardLoadedAt = Date.now();
    }).catch(error => {
        body.replaceChildren();
        const tr = document.createElement("tr");
        const td = document.createElement("td");
        td.colSpan = 6;
        td.className = "table-message";
        td.textContent = error.message || "Could not load the leaderboard.";
        tr.appendChild(td);
        body.appendChild(tr);
        $("lbUpdated").textContent = "Unavailable";
        $("lbFootnote").textContent = "Check that TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET are configured on the server, then refresh.";
    });
}

$("refreshLeaderboard").addEventListener("click", () => {
    leaderboardLoadedAt = 0;
    loadLeaderboard();
});

// ------------------------------------------------------------------ start

function handleSignInResult() {
    const params = new URLSearchParams(location.search);
    const result = params.get("clip_auth");
    if (!result) return;

    const reason = params.get("reason");
    history.replaceState(null, "", location.pathname);

    if (result === "ok") {
        toast("Twitch connected. You can clip now.", "ok");
        return;
    }

    const messages = {
        denied: "You cancelled the Twitch sign-in.",
        state: "That sign-in link expired. Connect again.",
        scope: "Twitch didn't allow clip creation. Connect again and approve every permission.",
        config: "The Twitch app keys on the server are missing or wrong.",
        exchange: "Twitch didn't accept the sign-in. Check the client secret and redirect URL, then connect again.",
    };
    toast(messages[reason] || messages.exchange, "err", { ttl: 12000 });
    setTimeout(openSetup, 700);
}

trace.start();
renderChat([], false);
loadSevenTVEmotes();
connect();
handleSignInResult();

})();
