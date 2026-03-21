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
    let _lastTopicState = {};

    function _serialize(value) {
        try {
            return JSON.stringify(value);
        } catch {
            return undefined;
        }
    }

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
        const globalSerialized = _serialize(global);
        for (const [topic, handler] of Object.entries(_handlers)) {
            if (state[topic] !== undefined) {
                const serialized = _serialize(state[topic]);
                if (_lastTopicState[topic] === serialized) {
                    continue;
                }
                _lastTopicState[topic] = serialized;
                try { handler(state[topic], global); } catch (e) { console.error(`Handler error [${topic}]:`, e); }
            }
        }
        // Also call a special "global" handler if registered.
        if (_handlers.global) {
            if (_lastTopicState.global !== globalSerialized) {
                _lastTopicState.global = globalSerialized;
                try { _handlers.global(global); } catch (e) { console.error("Handler error [global]:", e); }
            }
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
        const resolvedUrl = new URL(url, location.href).href;
        if (el.src !== resolvedUrl) {
            el.src = url;
        }
        if (alt && el.alt !== alt) {
            el.alt = alt;
        }
    }
}

function setClass(id, className, condition) {
    const el = document.getElementById(id);
    if (!el) return;
    // Remove all energy/status classes then apply the right one
    el.className = el.className.replace(/\benergy-\w+\b|\bstatus-\w+\b/g, "").trim();
    if (condition !== false) el.classList.add(className);
}


// ── Calendar column builder ─────────────────────────────────────────────────
// compact=true  → omit location; used when the calendar panel is small
// compact=false → full event card including location

function BuildCalendarColumns(container, days, compact = false) {
    container.innerHTML = '';
    const colH = container.clientHeight;

    // Measure header height with an off-screen probe so we know the spacer size
    const probe = document.createElement('div');
    probe.className = 'calendar-day-header';
    probe.innerHTML = '<div class="calendar-day-number">00</div><div class="calendar-day-name">Mon</div>';
    Object.assign(probe.style, { position: 'absolute', left: '-9999px', width: '160px', visibility: 'hidden' });
    document.body.appendChild(probe);
    const HEADER_H = probe.offsetHeight + 8;  // +8 for margin-bottom below header
    document.body.removeChild(probe);

    // Staging area used to measure individual event heights before placing them
    const stage = document.createElement('div');
    Object.assign(stage.style, { position: 'absolute', left: '-9999px', width: '160px', visibility: 'hidden' });
    document.body.appendChild(stage);

    function newCol(addSpacer) {
        const el = document.createElement('div');
        el.className = 'cal-column';
        container.appendChild(el);
        if (addSpacer) {
            const spacer = document.createElement('div');
            spacer.style.height = HEADER_H + 'px';
            spacer.style.flexShrink = '0';
            el.appendChild(spacer);
        }
        return el;
    }

    for (const day of days) {
        // Each day always starts in a fresh column (no spacer — header goes here)
        let col = newCol(false);
        let usedH = HEADER_H;

        const headerEl = document.createElement('div');
        headerEl.className = 'calendar-day-header';
        headerEl.innerHTML =
            `<div class="calendar-day-number">${day.day_number || '--'}</div>` +
            `<div class="calendar-day-name">${day.day_name || '------'}</div>`;
        col.appendChild(headerEl);

        for (const ev of (day.events || [])) {
            const locationHtml = (!compact && ev.location)
                ? `<div class="calendar-event-location">📍 ${ev.location}</div>`
                : '';
            const inner =
                `<div class="calendar-event-content">` +
                `<div class="calendar-event-time">${ev.time || ''}</div>` +
                `<div class="calendar-event-title">${ev.title || 'Untitled Event'}</div>` +
                locationHtml +
                `</div>`;

            // Measure in staging area (consistent width, out of view)
            stage.innerHTML = `<div class="calendar-event">${inner}</div>`;
            const evH = stage.firstChild.offsetHeight + 8;  // +8 for margin-bottom

            if (colH > 0 && usedH + evH > colH) {
                if (col.children.length === 1) {
                    // Only the header is in this column — the first event doesn't fit.
                    // Discard the empty column and move the header into a fresh one so
                    // we don't leave a header-only column followed by a spacer column.
                    container.removeChild(col);
                    col = newCol(false);
                    col.appendChild(headerEl);
                } else {
                    // Events already placed — start an overflow column with a spacer
                    // so events align with the first-event row of the header column.
                    col = newCol(true);
                }
                usedH = HEADER_H;
            }

            const evEl = document.createElement('div');
            evEl.className = 'calendar-event';
            evEl.style.cssText = `--calendar-color: ${ev.color || 'rgba(255,255,255,0.5)'}`;
            evEl.innerHTML = inner;
            col.appendChild(evEl);
            usedH += evH;
        }
    }

    document.body.removeChild(stage);
}
