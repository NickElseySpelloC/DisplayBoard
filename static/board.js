/**
 * board.js — shared WebSocket client for all DisplayBoard templates.
 *
 * Connects to /ws, receives state_update messages, and dispatches
 * data to registered topic handlers.
 *
 * Each board template includes this script and registers topic
 * handlers via: BoardClient.onTopic('weather', myHandler)
 *
 * State update message format (from controller.get_webapp_data()):
 *   { type: "state_update", state: { global: {...}, weather: {...}, ... } }
 */

const BoardClient = (() => {
    const RECONNECT_DELAY_MS = 3000;

    let _socket = null;
    let _handlers = {};
    let _statusEl = null;

    // ── Public API ──────────────────────────────────────────────────────

    function onTopic(topic, handler) {
        _handlers[topic] = handler;
    }

    function sendCommand(action, payload = {}) {
        if (_socket && _socket.readyState === WebSocket.OPEN) {
            _socket.send(JSON.stringify({ type: "command", action, ...payload }));
        }
    }

    function navigateBoard(direction, boardCount) {
        // Boards are served as separate pages at /?board=N
        const params = new URLSearchParams(location.search);
        const current = parseInt(params.get("board") || "0", 10);
        const count = boardCount || parseInt(document.getElementById("board-count")?.dataset.count || "2", 10);
        let next = direction === "next" ? current + 1 : current - 1;
        next = ((next % count) + count) % count;  // wrap around
        params.set("board", next);
        location.search = params.toString();
    }

    // ── WebSocket lifecycle ─────────────────────────────────────────────

    function _connect() {
        const proto = location.protocol === "https:" ? "wss" : "ws";
        const key = new URLSearchParams(location.search).get("key");
        const url = `${proto}://${location.host}/ws${key ? "?key=" + encodeURIComponent(key) : ""}`;

        _socket = new WebSocket(url);

        _socket.onopen = () => {
            _setStatus(true);
        };

        _socket.onmessage = (event) => {
            let msg;
            try { msg = JSON.parse(event.data); } catch { return; }

            if (msg.type === "state_update" && msg.state) {
                _dispatch(msg.state);
            }
        };

        _socket.onclose = () => {
            _setStatus(false);
            setTimeout(_connect, RECONNECT_DELAY_MS);
        };

        _socket.onerror = () => {
            _socket.close();
        };
    }

    function _dispatch(state) {
        // Call each registered handler with its slice of the state.
        // Always pass "global" data to every handler as second arg.
        const global = state.global || {};
        for (const [topic, handler] of Object.entries(_handlers)) {
            if (state[topic] !== undefined) {
                try { handler(state[topic], global); } catch (e) { console.error(`Handler error [${topic}]:`, e); }
            }
        }
        // Also call a special "global" handler if registered.
        if (_handlers.global) {
            try { _handlers.global(global); } catch (e) { console.error("Handler error [global]:", e); }
        }
    }

    function _setStatus(connected) {
        if (!_statusEl) _statusEl = document.getElementById("ws-status");
        if (_statusEl) {
            _statusEl.classList.toggle("connected", connected);
        }
    }

    // ── Init ────────────────────────────────────────────────────────────

    document.addEventListener("DOMContentLoaded", _connect);

    return { onTopic, sendCommand, navigateBoard };
})();


// ── Pagination helpers (used by boards that include pagination) ─────────────

function prevBoard() { BoardClient.navigateBoard("prev", _boardCount()); }
function nextBoard() { BoardClient.navigateBoard("next", _boardCount()); }
function _boardCount() {
    return parseInt(document.querySelector("[data-board-count]")?.dataset.boardCount || "2", 10);
}


// ── Utility: set text content of an element safely ─────────────────────────

function setText(id, value, fallback = "—") {
    const el = document.getElementById(id);
    if (el) el.textContent = (value !== null && value !== undefined) ? value : fallback;
}

function setImage(id, url, alt = "") {
    const el = document.getElementById(id);
    if (!el) return;
    if (url && url !== null && url !== undefined) {
        el.src = url;
        if (alt) el.alt = alt;
    }
}

function setClass(id, className, condition) {
    const el = document.getElementById(id);
    if (!el) return;
    // Remove all energy/status classes then apply the right one
    el.className = el.className.replace(/\benergy-\w+\b|\bstatus-\w+\b/g, "").trim();
    if (condition !== false) el.classList.add(className);
}
