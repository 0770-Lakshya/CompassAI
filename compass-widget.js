/*
 * compass-widget.js — the embeddable Compass widget.
 *
 * Loads as a single <script> tag. Injects a floating button + chat panel
 * into a Shadow DOM (so the host site's CSS can't touch it and vice versa).
 * On a question, calls the Compass API, then scrolls to and highlights the
 * returned element.
 *
 * Local test:
 *   add  <script src="compass-widget.js"></script>  to a saved page,
 *   serve the folder with:  python -m http.server 5500
 *   open  http://localhost:5500/index.html
 *
 * Config via data-attributes on the script tag:
 *   data-api   API base URL (default http://localhost:8000)
 */

(function () {
  "use strict";

  // ---- find our own <script> tag to read config ----
  const me = document.currentScript;
  const API = (me && me.dataset.api) || "https://compassai-vkoe.onrender.com";
  const SITE = (me && me.dataset.site) || location.hostname;

  // ---- create an isolated host element ----
  const host = document.createElement("div");
  host.id = "compass-widget-host";
  host.style.all = "initial";            // stop host page inheritance
  document.body.appendChild(host);
  const root = host.attachShadow({ mode: "open" });

  // ---- everything inside the shadow root ----
  root.innerHTML = `
    <style>
      :host { all: initial; }
      * { box-sizing: border-box; font-family: system-ui, -apple-system, sans-serif; }

      .fab {
        position: fixed; bottom: 24px; right: 24px; width: 56px; height: 56px;
        border-radius: 50%; border: none; cursor: pointer; z-index: 2147483647;
        background: #1f6f5c; color: #fff; font-size: 24px;
        box-shadow: 0 4px 16px rgba(0,0,0,.25);
        display: flex; align-items: center; justify-content: center;
        transition: transform .15s;
      }
      .fab:hover { transform: scale(1.06); }

      .panel {
        position: fixed; bottom: 92px; right: 24px; width: 340px; max-width: calc(100vw - 48px);
        background: #fff; border-radius: 14px; z-index: 2147483647;
        box-shadow: 0 8px 32px rgba(0,0,0,.22);
        display: none; flex-direction: column; overflow: hidden;
        border: 1px solid #e5e7eb;
      }
      .panel.open { display: flex; }

      .head {
        background: #1f6f5c; color: #fff; padding: 14px 16px;
        font-weight: 600; font-size: 15px;
      }
      .head small { display: block; font-weight: 400; opacity: .8; font-size: 12px; margin-top: 2px; }

      .body { padding: 14px 16px; min-height: 60px; font-size: 14px; color: #1a1a1a; line-height: 1.5; }
      .body .muted { color: #6b7280; }
      .body a { color: #1f6f5c; }

      .inputRow { display: flex; gap: 8px; padding: 12px 16px; border-top: 1px solid #eee; }
      .inputRow input {
        flex: 1; padding: 10px 12px; border: 1px solid #d1d5db; border-radius: 8px;
        font-size: 14px; outline: none;
      }
      .inputRow input:focus { border-color: #1f6f5c; }
      .inputRow button {
        border: none; background: #1f6f5c; color: #fff; border-radius: 8px;
        padding: 0 14px; cursor: pointer; font-size: 14px;
      }
      .inputRow button:disabled { opacity: .5; cursor: default; }

      .foot { text-align: center; font-size: 11px; color: #9ca3af; padding: 6px; }
    </style>

    <button class="fab" title="Ask Compass">🧭</button>

    <div class="panel">
      <div class="head">
        Ask Compass
        <small>Tell me what you're looking for</small>
      </div>
      <div class="body">
        <span class="muted">Try: "where are the projects" or "learn game development"</span>
      </div>
      <div class="inputRow">
        <input type="text" placeholder="Ask a question..." />
        <button class="send">Go</button>
      </div>
      <div class="foot">powered by Compass</div>
    </div>
  `;

  const fab = root.querySelector(".fab");
  const panel = root.querySelector(".panel");
  const input = root.querySelector("input");
  const sendBtn = root.querySelector(".send");
  const body = root.querySelector(".body");

  // ---- open / close ----
  fab.addEventListener("click", () => {
    panel.classList.toggle("open");
    if (panel.classList.contains("open")) input.focus();
  });

  // ---- highlight an element on the host page ----
  function highlight(selector) {
    let el;
    try {
      el = document.querySelector(selector);
    } catch (e) {
      return false;                      // malformed selector
    }
    if (!el) return false;

    el.scrollIntoView({ behavior: "smooth", block: "center" });

    // inject a keyframe + outline into the HOST page (not shadow) so it
    // wraps the real element. Scoped by a unique attribute so we clean up.
    const prevOutline = el.style.outline;
    const prevOffset = el.style.outlineOffset;
    const prevTransition = el.style.transition;

    el.style.transition = "outline-color .3s";
    el.style.outline = "3px solid #1f6f5c";
    el.style.outlineOffset = "4px";

    // pulse: fade the outline out after a moment
    setTimeout(() => {
      el.style.outline = "3px solid transparent";
    }, 1800);
    setTimeout(() => {
      el.style.outline = prevOutline;
      el.style.outlineOffset = prevOffset;
      el.style.transition = prevTransition;
    }, 2200);

    return true;
  }

  // ---- ask the API ----
  async function ask(q) {
    body.innerHTML = `<span class="muted">Looking...</span>`;
    sendBtn.disabled = true;
    try {
      const res = await fetch(API + "/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: q, site_id: SITE }),
      });
      const data = await res.json();

      if (!data.found) {
        const notRegistered = (data.reason || "").includes("not registered");
        body.innerHTML = notRegistered
          ? `<span class="muted">Compass isn't set up for this site yet.</span>`
          : `<span class="muted">I couldn't find that on this page. Try rephrasing?</span>`;
        return;
      }

      const samePage =
        !data.url || data.url.replace(/\/$/, "") === location.href.replace(/\/$/, "").split("#")[0];

      if (samePage) {
        const ok = highlight(data.selector);
        body.innerHTML = ok
          ? `${escapeHtml(data.explanation)} <br><span class="muted">↑ taking you there</span>`
          : `${escapeHtml(data.explanation)} <br><span class="muted">(couldn't locate it on this page)</span>`;
      } else {
        // different page: stash selector, navigate, resume on load
        sessionStorage.setItem("compass_target", data.selector);
        body.innerHTML = `${escapeHtml(data.explanation)} <br><a href="${data.url}">Take me there →</a>`;
      }
    } catch (e) {
      body.innerHTML = `<span class="muted">Something went wrong reaching Compass.</span>`;
    } finally {
      sendBtn.disabled = false;
    }
  }

  function escapeHtml(s) {
    return (s || "").replace(/[&<>"]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])
    );
  }

  // ---- submit handlers ----
  function submit() {
    const q = input.value.trim();
    if (q) ask(q);
  }
  sendBtn.addEventListener("click", submit);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") submit();
  });

  // ---- resume after cross-page navigation ----
  const pending = sessionStorage.getItem("compass_target");
  if (pending) {
    sessionStorage.removeItem("compass_target");
    // wait a beat for the new page to finish rendering
    setTimeout(() => highlight(pending), 600);
  }
})();