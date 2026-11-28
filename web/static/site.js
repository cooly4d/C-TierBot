// C-TierBot website motion and chart interaction. Progressive enhancement only: the server renders every value,
// dot and bar in its final state; this script sets a starting state just before animating it. With
// prefers-reduced-motion nothing moves or counts (the crosshair, tooltip and in-place switches still work).
// Text from the page is set with textContent / setAttribute, never innerHTML.
import { animate, cubicBezier } from "./vendor/anime.esm.min.js";

const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const EASE_OUT = cubicBezier(0.16, 1, 0.3, 1);
const EASE_REVEAL = cubicBezier(0.85, 0, 0.15, 1); // bklit's reveal curve
const SVG_NS = "http://www.w3.org/2000/svg";

// A number shown on the page ("1,250", "2.5") back as a number, for counting from one shown value to another.
const shownNumber = (text) => Number(String(text ?? "").replace(/,/g, ""));

// The queue slot a chart mark belongs to (dot, ring, loss hole or latest ring).
const slotOf = (el) => Number(el.dataset.dot ?? el.dataset.ring ?? el.dataset.hole ?? el.dataset.latest);
// "Queue #4821", or just "Queue" when the stored number isn't one the site shows.
const queueName = (slot) => (slot.match_id ? `Queue #${slot.match_id}` : "Queue");

const formats = {
  int: (v) => Math.round(v).toLocaleString("en-US"),
  damage: (v) => Math.floor(v + 0.5).toLocaleString("en-US"),
  impact: (v) => String(Math.floor(v + 0.5)),
  kills: (v) => v.toFixed(1),
  playtime: (ms) => {
    const minutes = Math.floor(ms / 60000);
    return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m`;
  },
};

// Run `fn` once, the first time `el` is on screen.
function onceVisible(el, fn) {
  if (!("IntersectionObserver" in window)) { fn(); return; }
  const io = new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting)) { io.disconnect(); fn(); }
  }, { threshold: 0.25 });
  io.observe(el);
}

// Count a number up from 0 to its rendered value. Only the element's first text node changes, so screen-reader
// text inside it stays.
function countUp(el, target, format, duration = 900, delay = 0) {
  const fmt = formats[format] || formats.int;
  const node = el.firstChild && el.firstChild.nodeType === Node.TEXT_NODE ? el.firstChild : null;
  if (!node || !Number.isFinite(target)) return;
  const finalText = node.nodeValue;
  const state = { v: 0 };
  node.nodeValue = fmt(0);
  animate(state, {
    v: target, duration, delay, ease: EASE_OUT,
    onUpdate: () => { node.nodeValue = fmt(state.v); },
    onComplete: () => { node.nodeValue = finalText; },
  });
}

// --- Queue page: the match timeline plays the series --------------------------------------------------------
function initTimeline() {
  const tl = document.querySelector("[data-timeline]");
  if (!tl) return;
  if (tl.hasAttribute("data-replay") && !reduced) {
    onceVisible(tl, () => playTimeline(tl));
  }
}

function playTimeline(tl) {
  const winners = (tl.dataset.winners || "").split(",").map((w) => (w === "" ? null : Number(w)));
  const digits = [...document.querySelectorAll(".series-score [data-digit]")];
  const counts = digits.map(() => 0);
  digits.forEach((d) => { d.textContent = "0"; });
  const base = 250, gap = 140, dur = 380;
  const dots = [...tl.querySelectorAll("[data-dot]")].sort((a, b) => a.dataset.dot - b.dataset.dot);
  dots.forEach((dot) => {
    const i = Number(dot.dataset.dot);
    const lane = dot.dataset.lane === undefined ? null : Number(dot.dataset.lane);
    const from = lane === null ? 0 : lane === 0 ? 18 : -18; // T1 rises from the midline, T2 falls to it
    animate(dot, {
      opacity: [0, 1], translateY: [from, 0], scale: [0.35, 1],
      duration: dur, delay: base + i * gap, ease: EASE_OUT,
      onComplete: () => {
        const w = winners[i];
        if (w === null || w === undefined || !digits[w]) return;
        counts[w] += 1;
        digits[w].textContent = String(counts[w]);
        animate(digits[w], { scale: [1, 1.14, 1], duration: 220, ease: EASE_OUT });
      },
    });
  });
  const end = base + Math.max(0, dots.length - 1) * gap + dur;
  document.querySelectorAll("[data-mvp]").forEach((pill) => {
    animate(pill, { opacity: [0, 1], duration: 240, delay: end, ease: EASE_OUT });
  });
}

// --- Queue page: game buttons (and the timeline's dots) swap the game tables in place ---------------------------
const plainClick = (event) => !(event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0);

function initGames() {
  const nav = document.querySelector("[data-game-pills]");
  if (!nav) return;
  nav.addEventListener("click", (event) => {
    const link = event.target.closest("a[data-game]");
    if (!link || !plainClick(event)) return;
    event.preventDefault();
    selectGame(link.dataset.game);
  });
  // A timeline dot picks its game the same way, then brings the breakdown into view if it's off screen (the link's
  // own #games jump, without the reload).
  document.querySelector("[data-timeline]")?.addEventListener("click", (event) => {
    const dot = event.target.closest("a[data-game]");
    if (!dot || !plainClick(event)) return;
    event.preventDefault();
    selectGame(dot.dataset.game);
    revealGame();
    // From the keyboard (Enter gives a click with no pointer detail), focus follows to the selected game's button,
    // so the next Tab carries on into the breakdown instead of back to the next dot.
    if (event.detail === 0) {
      document.querySelector(`[data-game-pills] a[data-game="${dot.dataset.game}"]`)?.focus({ preventScroll: true });
    }
  });
}

function revealGame() {
  const panel = document.querySelector("[data-game-panel]:not([hidden])");
  if (!panel || panel.getBoundingClientRect().top < window.innerHeight * 0.75) return;
  document.getElementById("games")?.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
}

// A team panel's winner colours, as currently drawn (border, head background, head text, head rule).
function panelColours(team) {
  const head = team.querySelector(".team-head");
  const t = getComputedStyle(team), h = getComputedStyle(head);
  return { border: t.borderTopColor, headBg: h.backgroundColor, headText: h.color, headRule: h.borderBottomColor };
}

function setPanelColours(team, c) {
  const head = team.querySelector(".team-head");
  team.style.borderColor = c ? c.border : "";
  head.style.backgroundColor = c ? c.headBg : "";
  head.style.color = c ? c.headText : "";
  head.style.borderBottomColor = c ? c.headRule : "";
}

function selectGame(number) {
  const panels = document.querySelectorAll("[data-game-panel]");
  const outgoing = document.querySelector("[data-game-panel]:not([hidden])");
  const before = outgoing ? [...outgoing.querySelectorAll(".team")].map(panelColours) : [];
  let shown = null;
  panels.forEach((panel) => {
    const match = panel.dataset.gamePanel === number;
    panel.hidden = !match;
    if (match) shown = panel;
  });
  document.querySelectorAll("[data-game-pills] a[data-game]").forEach((a) => {
    if (a.dataset.game === number) a.setAttribute("aria-current", "true");
    else a.removeAttribute("aria-current");
  });
  const index = [...document.querySelectorAll("[data-game-pills] a[data-game]")].findIndex((a) => a.dataset.game === number);
  document.querySelectorAll(".timeline [data-dot]").forEach((dot) => {
    const on = Number(dot.dataset.dot) === index;
    dot.classList.toggle("is-selected", on);
    if (on) dot.setAttribute("aria-current", "true");
    else dot.removeAttribute("aria-current");
  });
  document.querySelectorAll(".timeline [data-label]").forEach((label) => label.classList.toggle("is-selected", Number(label.dataset.label) === index));
  const url = new URL(window.location.href);
  url.searchParams.set("game", number);
  url.hash = "games";
  history.replaceState(null, "", url);
  if (shown && shown !== outgoing) {
    // Start each panel in the outgoing game's colours, then let the CSS colour transition (240 ms, kept under
    // reduced motion) carry it to this game's winner colours.
    const teams = [...shown.querySelectorAll(".team")];
    teams.forEach((team, k) => { if (before[k]) setPanelColours(team, before[k]); });
    shown.getBoundingClientRect();
    teams.forEach((team) => setPanelColours(team, null));
  }
  if (shown && !reduced) animate(shown, { opacity: [0, 1], translateY: [6, 0], duration: 260, ease: EASE_OUT });
}

// --- Leaderboard and home: bars grow, values count up; rows keep their identity across rank changes ------------
// Leaderboard rows keep their identity across "Rank by" and month changes (leaderboard.css names them from
// data-vt with attr()). Where attr() can't, name them here. Only leaderboard pages name rows.
const onLeaderboard = location.pathname === "/leaderboard";
const cssNamesRows = Boolean(window.CSS && CSS.supports
  && CSS.supports("view-transition-name", "attr(data-vt type(<custom-ident>), none)"));

function nameRows() {
  if (!onLeaderboard || cssNamesRows) return;
  document.querySelectorAll(".board-row[data-vt]").forEach((row) => { row.style.viewTransitionName = row.dataset.vt; });
}

function initPageTransitions() {
  // Leaving a leaderboard page for any other page: drop the names, so the rows fade with the page instead of
  // flying to wherever the next page has room.
  window.addEventListener("pageswap", (event) => {
    if (!event.viewTransition) return;
    let to = null;
    try { to = new URL(event.activation.entry.url).pathname; } catch (_) { /* destination unknown */ }
    if (onLeaderboard && to === "/leaderboard") return;
    document.documentElement.classList.add("vt-plain");
    document.querySelectorAll(".board-row").forEach((row) => { row.style.viewTransitionName = ""; });
  });
  // Back to this page from the browser's page cache: names on again.
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    document.documentElement.classList.remove("vt-plain");
    nameRows();
  });
}

function initBoards() {
  nameRows();
  document.querySelectorAll("[data-board]").forEach((board) => {
    const rows = [...board.querySelectorAll(".board-row")];
    if (reduced) return;
    let fromBoard = false;
    try { fromBoard = new URL(document.referrer).pathname === "/leaderboard" && location.pathname === "/leaderboard"; } catch (_) { /* no referrer */ }
    if (fromBoard) return; // arriving by a rank-by/month change: the rows slide instead (leaderboard.css)
    onceVisible(board, () => {
      const format = board.dataset.format || "damage";
      rows.slice(0, 10).forEach((row, i) => {
        const fill = row.querySelector(".bar-fill");
        if (fill) {
          fill.style.transformBox = "fill-box";
          fill.style.transformOrigin = "0 50%";
          animate(fill, { scaleX: [0, 1], duration: 500, delay: i * 30, ease: EASE_OUT });
        }
        // The animated number is an aria-hidden copy; screen readers read the final value next to it.
        const value = row.querySelector("[data-count]");
        if (value) countUp(value, Number(value.dataset.count), format === "kills" ? "kills" : format, 500, i * 30);
      });
    });
  });
}

// --- Player page: the trend chart ------------------------------------------------------------------------------
function initTrend() {
  const root = document.querySelector("[data-trend]");
  if (!root) return;
  let data;
  try { data = JSON.parse(root.querySelector("[data-trend-json]").textContent); } catch (_) { return; }
  const chart = new TrendChart(root, data, root.dataset.metric);
  if (!reduced) onceVisible(root, () => chart.reveal());
}

class TrendChart {
  constructor(root, data, metric) {
    this.root = root;
    this.data = data;
    this.metric = metric;
    this.plot = root.querySelector("[data-trend-plot]");
    this.line = root.querySelector("[data-trend-line]");
    this.area = root.querySelector("[data-trend-area]");
    this.dotsGroup = root.querySelector("[data-trend-dots]");
    this.cross = root.querySelector("[data-trend-cross]");
    this.hits = root.querySelector("[data-trend-hits]");
    this.tip = root.querySelector("[data-trend-tip]");
    this.active = -1;
    // What the chart shows right now, per queue slot, so a switch made mid-tween starts from the screen.
    this.shownY = data[metric].slots.map((s) => s.y);
    this.tween = null; // the running metric tween
    this.fades = []; // dots fading out or in for the current switch
    this.generation = 0; // a superseded switch's callbacks see a newer number and write nothing
    this.plot.addEventListener("pointerleave", () => this.clear());
    this.bindHits();
    root.querySelectorAll("a[data-metric]").forEach((a) => a.addEventListener("click", (event) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
      event.preventDefault();
      this.switchTo(a.dataset.metric);
    }));
  }

  bindHits() {
    this.hits.querySelectorAll("button.hit").forEach((button) => {
      const i = Number(button.dataset.i);
      button.addEventListener("pointerenter", () => this.setActive(i));
      button.addEventListener("focus", () => this.setActive(i));
      button.addEventListener("blur", (event) => {
        const next = event.relatedTarget;
        if (!(next && next.classList && next.classList.contains("hit"))) this.clear();
      });
      button.addEventListener("keydown", (event) => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        const buttons = [...this.hits.querySelectorAll("button.hit")];
        const at = buttons.indexOf(button);
        const next = buttons[at + (event.key === "ArrowRight" ? 1 : -1)];
        if (next) { event.preventDefault(); next.focus(); }
      });
    });
  }

  reveal() {
    const g = this.root.querySelector("[data-trend-reveal]");
    animate(g, { clipPath: ["inset(0 100% 0 0)", "inset(0 0% 0 0)"], duration: 1100, ease: EASE_REVEAL });
    [...this.dotsGroup.children].forEach((dot) => {
      const slot = this.data[this.metric].slots[slotOf(dot)];
      animate(dot, { opacity: [0, 1], duration: 280, delay: 120 + (slot.x / 1000) * 900, ease: EASE_OUT });
    });
    const latest = this.root.querySelector("[data-trend-latest-wrap]");
    if (latest && !latest.hidden) animate(latest, { opacity: [0, 1], duration: 220, delay: 1150, ease: EASE_OUT });
  }

  setActive(i) {
    const slot = this.data[this.metric].slots[i];
    if (!slot || slot.y === null) return;
    const appearing = this.active === -1;
    this.active = i;
    this.root.classList.add("has-active");
    // The crosshair glides from point to point (site.css); when it first appears it snaps into place.
    if (appearing) this.cross.classList.add("is-snapping");
    this.cross.style.transform = `translateX(${slot.x}px)`;
    if (appearing) {
      this.cross.getBoundingClientRect();
      this.cross.classList.remove("is-snapping");
    }
    this.dotsGroup.querySelectorAll(".is-active").forEach((el) => el.classList.remove("is-active"));
    this.dotsGroup.querySelectorAll(`[data-dot="${i}"], [data-ring="${i}"]`).forEach((el) => el.classList.add("is-active"));
    const won = slot.won === null ? "result unknown" : `${slot.won ? "won" : "lost"} ${slot.score || ""}`.trim();
    this.tip.querySelector("[data-tip-value]").textContent = slot.text;
    this.tip.querySelector("[data-tip-unit]").textContent = this.data[this.metric].unit;
    this.tip.querySelector("[data-tip-line]").textContent = `${queueName(slot)} · ${won}`;
    const key = this.tip.querySelector("[data-tip-key]");
    key.className = "tip-key" + (slot.won === null ? "" : slot.won ? " state-win" : " state-lose");
    this.tip.hidden = false;
    const w = this.plot.clientWidth, h = this.plot.clientHeight;
    const px = (slot.x / 1000) * w, py = (slot.y / 300) * h;
    const tipW = this.tip.offsetWidth, tipH = this.tip.offsetHeight;
    const left = px > w * 0.7 ? px - tipW - 16 : px + 16;
    const top = Math.max(0, Math.min(h - tipH, py - tipH / 2));
    this.tip.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
  }

  clear() {
    this.active = -1;
    this.root.classList.remove("has-active");
    this.dotsGroup.querySelectorAll(".is-active").forEach((el) => el.classList.remove("is-active"));
    this.tip.hidden = true;
  }

  // Switch metric without a reload: points are matched by queue (slot), never by array position. Shared points
  // move, points missing in the new metric fade out, new ones fade in, the line breaks across missing points, and
  // an all-missing metric shows its empty message.
  switchTo(metric) {
    if (metric === this.metric || !this.data[metric]) return;
    const from = this.data[this.metric], to = this.data[metric];
    const focusedIndex = document.activeElement && document.activeElement.classList.contains("hit")
      ? Number(document.activeElement.dataset.i) : null;
    this.metric = metric;
    this.root.dataset.metric = metric;
    this.root.querySelectorAll("a[data-metric]").forEach((a) => {
      if (a.dataset.metric === metric) a.setAttribute("aria-current", "true"); else a.removeAttribute("aria-current");
    });
    this.root.querySelector("[data-trend-title]").textContent = to.title;
    const latestWrap = this.root.querySelector("[data-trend-latest-wrap]");
    const latestEl = this.root.querySelector("[data-trend-latest]");
    const latestShownText = latestWrap.hidden ? null : latestEl.textContent; // mid-count text counts on from here
    latestWrap.hidden = to.empty;
    latestEl.textContent = to.latest || "";
    this.root.querySelector("[data-trend-unit]").textContent = to.unit;
    const empty = this.root.querySelector("[data-trend-empty]");
    empty.textContent = to.empty_text;
    empty.hidden = !to.empty;
    // Rebuild the tick labels from the destination metric (an empty metric has none to update in place).
    const tickShownTexts = [...this.root.querySelectorAll("[data-trend-ticks] [data-tick]")].map((el) => el.textContent);
    const tickSpans = to.ticks.map((t, k) => {
      const span = document.createElement("span");
      span.className = `tick tick-${Math.round(t.fraction * 100)}`;
      span.dataset.tick = String(k);
      span.textContent = t.label;
      return span;
    });
    this.root.querySelector("[data-trend-ticks]").replaceChildren(...tickSpans);
    this.root.querySelector("[data-trend-grid]").setAttribute("d", to.ticks.map((t) => `M0 ${t.y} H1000`).join(" "));
    document.querySelectorAll(".pager a[href]").forEach((a) => {
      const link = new URL(a.getAttribute("href"), window.location.href);
      link.searchParams.set("metric", metric);
      a.setAttribute("href", `${link.pathname}${link.search}`);
    });
    const header = document.querySelectorAll(".history thead th[data-col]");
    header.forEach((th) => th.classList.toggle("accent", th.dataset.col === metric));
    this.clear();
    this.rebuildHits(to);
    this.tweenGeometry(to, this.countedLabels(to, tickSpans, tickShownTexts, latestEl, latestShownText));
    const url = new URL(window.location.href);
    url.searchParams.set("metric", metric);
    history.replaceState(null, "", url);
    if (focusedIndex !== null) this.restoreFocus(focusedIndex, metric);
  }

  restoreFocus(index, metric) {
    const buttons = [...this.hits.querySelectorAll("button.hit")];
    if (!buttons.length) {
      const link = this.root.querySelector(`a[data-metric="${metric}"]`);
      if (link) link.focus();
      return;
    }
    let best = buttons[0];
    buttons.forEach((b) => { if (Math.abs(Number(b.dataset.i) - index) < Math.abs(Number(best.dataset.i) - index)) best = b; });
    best.focus();
  }

  rebuildHits(to) {
    const unit = to.unit;
    this.hits.replaceChildren(...to.slots.map((slot) => {
      if (slot.y === null) {
        const span = document.createElement("span");
        span.className = "hit-empty";
        span.dataset.i = String(slot.i);
        return span;
      }
      const button = document.createElement("button");
      button.type = "button";
      button.className = "hit";
      button.dataset.i = String(slot.i);
      const won = slot.won === null ? "result unknown" : `${slot.won ? "won" : "lost"} ${slot.score || ""}`.trim();
      button.setAttribute("aria-label", `${queueName(slot)}, ${won}, ${slot.text} ${unit}`);
      return button;
    }));
    this.bindHits();
  }

  paths(slots, ys) {
    const segments = [];
    let current = [];
    slots.forEach((slot, k) => {
      if (ys[k] === null) { if (current.length) segments.push(current); current = []; }
      else current.push([slot.x, ys[k]]);
    });
    if (current.length) segments.push(current);
    const full = segments.filter((s) => s.length >= 2);
    const line = full.map((s) => "M" + s.map((p) => `${p[0]} ${p[1].toFixed(2)}`).join(" L")).join(" ");
    const area = full.map((s) => "M" + s.map((p) => `${p[0]} ${p[1].toFixed(2)}`).join(" L") + ` L${s[s.length - 1][0]} 300 L${s[0][0]} 300 Z`).join(" ");
    return { line, area };
  }

  // Labels that count from what they showed (mid-count included) to the new metric's values during the line
  // tween: the axis ticks (same three positions in every metric) and the latest value. Each ends on the server's
  // exact text.
  countedLabels(to, tickSpans, tickShownTexts, latestEl, latestShownText) {
    const fmt = formats[this.metric] || formats.int; // this.metric is already the new one
    const items = [];
    if (tickShownTexts.length === to.ticks.length) {
      tickSpans.forEach((span, k) => items.push({ el: span, a: shownNumber(tickShownTexts[k]), b: shownNumber(to.ticks[k].label), text: to.ticks[k].label }));
    }
    if (latestShownText !== null && !to.empty) {
      items.push({ el: latestEl, a: shownNumber(latestShownText), b: shownNumber(to.latest), text: to.latest });
    }
    const usable = items.filter((it) => Number.isFinite(it.a) && Number.isFinite(it.b));
    return (t) => usable.forEach((it) => { it.el.textContent = t >= 1 ? it.text : fmt(it.a + (it.b - it.a) * t); });
  }

  makeDot(slot, cls, attr) {
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("class", cls);
    path.setAttribute("d", `M${slot.x} ${slot.y}h0`);
    path.setAttribute(attr, String(slot.i));
    return path;
  }

  tweenGeometry(to, labels = () => {}) {
    // A newer switch supersedes any running one: stop it and start from what is on screen now.
    const generation = ++this.generation;
    if (this.tween) { this.tween.cancel(); this.tween = null; }
    this.fades.forEach((a) => a.cancel());
    this.fades = [];
    const startY = this.shownY.slice();
    const endY = to.slots.map((s) => s.y);
    const old = [...this.dotsGroup.children];
    const fresh = [];
    const latest = to.latest_i; // the newest queue with a value for this metric
    to.slots.forEach((slot) => {
      if (slot.y === null) return;
      const state = slot.won === null ? "neutral" : slot.won ? "win" : "lose";
      if (slot.i === latest) fresh.push(this.makeDot(slot, "dot-latest", "data-latest"));
      fresh.push(this.makeDot(slot, "dot-ring", "data-ring"), this.makeDot(slot, `dot state-${state}`, "data-dot"));
      if (slot.won === false) fresh.push(this.makeDot(slot, "dot-hole", "data-hole")); // losses are hollow rings
    });
    const apply = (t) => {
      const ys = endY.map((y, k) => (y === null ? null : startY[k] === null ? y : startY[k] + (y - startY[k]) * t));
      this.shownY = ys;
      const { line, area } = this.paths(to.slots, ys);
      this.line.setAttribute("d", line);
      this.area.setAttribute("d", area);
      fresh.forEach((dot) => {
        const k = slotOf(dot);
        if (ys[k] !== null) dot.setAttribute("d", `M${to.slots[k].x} ${ys[k].toFixed(2)}h0`);
      });
    };
    if (reduced) {
      old.forEach((dot) => dot.remove());
      this.dotsGroup.replaceChildren(...fresh);
      apply(1);
      labels(1);
      return;
    }
    // Leaving points fade out in place (from however visible they are now); shared points move; entering points
    // fade in.
    old.forEach((dot) => {
      const k = slotOf(dot);
      if (endY[k] === null && startY[k] !== null) {
        const from = Number(getComputedStyle(dot).opacity);
        this.fades.push(animate(dot, { opacity: [from, 0], duration: 180, ease: EASE_OUT, onComplete: () => dot.remove() }));
      } else {
        dot.remove();
      }
    });
    fresh.forEach((dot) => {
      const k = slotOf(dot);
      this.dotsGroup.appendChild(dot);
      if (startY[k] === null) this.fades.push(animate(dot, { opacity: [0, 1], duration: 260, delay: 120, ease: EASE_OUT }));
    });
    const state = { t: 0 };
    apply(0);
    labels(0);
    this.tween = animate(state, {
      t: 1, duration: 500, ease: EASE_OUT,
      onUpdate: () => { if (generation === this.generation) { apply(state.t); labels(state.t); } },
      onComplete: () => {
        if (generation !== this.generation) return;
        apply(1);
        labels(1);
        this.tween = null;
      },
    });
  }
}

// --- Operator panel: the console runs in place ------------------------------------------------------------------
// Without this the form posts and the panel comes back with the output. Here each command is posted with
// fetch and its output appended, so the transcript builds up; Up and Down recall earlier commands (this page
// only, never stored).
function initConsole() {
  const root = document.querySelector("[data-console]");
  const form = root && root.querySelector("[data-console-form]");
  if (!form) return;
  const input = form.querySelector("input[name='command']");
  const submit = form.querySelector("[data-console-submit]");
  const out = root.querySelector("[data-console-out]");
  const tools = root.querySelector("[data-console-tools]");
  const chips = tools ? [...tools.querySelectorAll("[data-console-run], [data-console-clear]")] : [];
  const history = [];
  let recall = 0;
  let running = false;
  if (tools) tools.hidden = false;

  const toEnd = () => { out.scrollTop = out.scrollHeight; };
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  function entry(command) {
    out.querySelector("[data-console-empty]")?.remove();
    const box = el("div", "console-entry");
    const cmd = el("p", "console-cmd");
    const caret = el("span", "console-caret", ">");
    caret.setAttribute("aria-hidden", "true");
    cmd.append(caret, command);
    const text = el("pre", "console-text");
    text.append(el("span", "console-running", "Running…"));
    box.append(cmd, text);
    out.append(box);
    toEnd();
    if (!reduced) animate(box, { opacity: [0, 1], translateY: [4, 0], duration: 200, ease: EASE_OUT });
    return text;
  }

  function show(text, lines, link) {
    text.textContent = "";
    if (!lines.length) lines = [{ text: "(no output)", kind: "quiet" }];
    lines.forEach((line, k) => {
      text.append(el("span", line.kind ? `k-${line.kind}` : "", line.text));
      if (k < lines.length - 1) text.append("\n");
    });
    if (link) {
      const a = el("a", "", link.text);
      a.href = link.href;
      text.append("\n", a);
    }
  }

  // Each command gets its own run ID; the server never runs the same ID twice (a resubmitted form gets the first
  // run's output).
  function newRunId() {
    if (crypto.randomUUID) return crypto.randomUUID();
    return [...crypto.getRandomValues(new Uint8Array(16))].map((b) => b.toString(16).padStart(2, "0")).join("");
  }

  function setRunning(on) {
    running = on;
    submit.disabled = on; // the transcript's "Running…" line says why
    chips.forEach((chip) => { chip.disabled = on; }); // Clear too: it would drop the running command's output
    if (on) root.setAttribute("aria-busy", "true");
    else root.removeAttribute("aria-busy");
  }

  function clear() {
    out.replaceChildren(el("p", "console-empty", "Cleared."));
    out.firstChild.setAttribute("data-console-empty", "");
  }

  async function run(command) {
    command = command.trim();
    if (!command || running) return;
    if (history[history.length - 1] !== command) history.push(command);
    recall = history.length;
    if (command.split(/\s+/)[0].toLowerCase() === "clear") { clear(); return; }
    setRunning(true);
    const text = entry(command);
    try {
      const res = await fetch(form.action, {
        method: "POST", credentials: "same-origin", headers: { Accept: "application/json" },
        body: new URLSearchParams({ command, run: newRunId() }),
      });
      if (!res.ok || !(res.headers.get("content-type") || "").includes("application/json")) {
        // A signed-out request is redirected to the login page, which isn't JSON.
        throw new Error(res.redirected || res.ok ? "signed-out" : `HTTP ${res.status}`);
      }
      const data = await res.json();
      show(text, Array.isArray(data.lines) ? data.lines : []);
    } catch (err) {
      if (err.message === "signed-out") {
        show(text, [{ text: "Your session has ended, so nothing ran. Sign in again, then rerun it.", kind: "err" }],
             { text: "Sign in", href: "/admin/login" });
      } else {
        show(text, [{ text: `The bot didn't answer (${err.message}). If you just ran stop confirm, it's restarting.`, kind: "err" }]);
      }
    } finally {
      setRunning(false);
      toEnd();
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (running) return;
    const command = input.value;
    input.value = "";
    run(command);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowUp" && recall > 0) {
      recall -= 1;
    } else if (event.key === "ArrowDown" && recall < history.length) {
      recall += 1;
    } else {
      return;
    }
    event.preventDefault();
    input.value = history[recall] ?? "";
    input.setSelectionRange(input.value.length, input.value.length);
  });
  tools?.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    if (running) return;
    if (button.hasAttribute("data-console-clear")) clear();
    else if (button.dataset.consoleRun) run(button.dataset.consoleRun);
  });
  toEnd();
}

// --- Times in the visitor's own timezone -------------------------------------------------------------------------
// The server writes UTC ("26 Nov 2026, 23:58 UTC") with the instant in data-ms; this shows it in local time, with
// the UTC text kept as the tooltip.
const localFormat = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
const localDate = (ms) => localFormat.format(new Date(ms));
const dayKey = (date) => `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;

function initLocalTimes() {
  document.querySelectorAll("time[data-ms]").forEach((t) => {
    const ms = Number(t.dataset.ms);
    if (!Number.isFinite(ms)) return;
    t.title = t.textContent;
    t.textContent = localDate(ms);
  });
}

// The archive's day headings again, by the visitor's own calendar days (the server's are UTC days), so a queue
// played just after midnight sits under the same day its time shows.
function initDayHeadings() {
  const list = document.querySelector("[data-day-list]");
  if (!list) return;
  const now = new Date();
  const today = dayKey(now);
  const yesterday = dayKey(new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1));
  const label = (date) => {
    const key = dayKey(date);
    if (key === today) return "Today";
    if (key === yesterday) return "Yesterday";
    const text = `${date.getDate()} ${date.toLocaleString("en-GB", { month: "short" })}`;
    return date.getFullYear() === now.getFullYear() ? text : `${text} ${date.getFullYear()}`;
  };
  list.querySelectorAll(".q-day").forEach((h) => h.remove());
  let last = null;
  list.querySelectorAll(".q-row").forEach((row) => {
    // No data-ms: the queue's only date is when its result was saved (or none), so it stays in the day above.
    if (!row.dataset.ms && last !== null) return;
    const ms = Number(row.dataset.ms);
    const date = Number.isFinite(ms) && row.dataset.ms ? new Date(ms) : null;
    const key = date ? dayKey(date) : "";
    if (key === last) return;
    last = key;
    const li = document.createElement("li");
    li.className = "q-day";
    const h = document.createElement("h2");
    h.className = "q-day-title";
    h.textContent = date ? label(date) : "Date unknown";
    li.append(h);
    row.before(li);
  });
}

// --- Copy link (queue and player pages) ---------------------------------------------------------------------------
// Copies the address as it is when clicked, so a selected game or chart metric comes along. Says "Copied" only
// once the clipboard write has worked; hidden where the clipboard can't be written.
function initCopy() {
  if (!(navigator.clipboard && window.isSecureContext)) return;
  document.querySelectorAll("[data-copy]").forEach((button) => {
    const label = button.querySelector("[data-copy-label]");
    let reset = 0;
    button.hidden = false;
    button.addEventListener("click", async () => {
      clearTimeout(reset);
      try {
        await navigator.clipboard.writeText(window.location.href);
        label.textContent = "Copied";
        button.classList.add("is-done");
      } catch (_) {
        label.textContent = "Couldn't copy";
      }
      reset = setTimeout(() => { label.textContent = "Copy link"; button.classList.remove("is-done"); }, 1800);
    });
  });
}

// --- Queue page: Left and Right arrow keys open the previous and next queue ---------------------------------------
function initQueueKeys() {
  const pager = document.querySelector("[data-queue-pager]");
  if (!pager) return;
  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    // Leave the keys to text fields and to anything that scrolls sideways (wide tables, the timeline).
    if (event.target.closest && event.target.closest("input, textarea, select, [contenteditable='true'], .table-scroll, .timeline-grid")) return;
    const link = pager.querySelector(event.key === "ArrowLeft" ? "a[rel='prev']" : "a[rel='next']");
    if (link) window.location.href = link.href;
  });
}

// --- Header: scrolled shadow and the pointer's highlight on the glass --------------------------------------------
function initHeader() {
  const bar = document.querySelector("[data-bar]");
  if (!bar) return;
  const update = () => document.documentElement.classList.toggle("is-scrolled", window.scrollY > 8);
  update();
  window.addEventListener("scroll", update, { passive: true });
  if (reduced || !window.matchMedia("(hover: hover) and (pointer: fine)").matches) return;
  let frame = 0, x = 0, y = 0;
  bar.addEventListener("pointermove", (event) => {
    const box = bar.getBoundingClientRect();
    x = event.clientX - box.left;
    y = event.clientY - box.top;
    if (!frame) {
      frame = requestAnimationFrame(() => {
        frame = 0;
        bar.style.setProperty("--mx", `${Math.round(x)}px`);
        bar.style.setProperty("--my", `${Math.round(y)}px`);
      });
    }
  });
}

// --- "Detailed": every row's dates, durations and games played at once, remembered on this browser -------------
function initDetail() {
  const box = document.querySelector("[data-detail-toggle]");
  if (!box) return;
  const root = document.documentElement;
  box.checked = root.classList.contains("detailed"); // boot.js read the saved choice before the first paint
  box.addEventListener("change", () => {
    root.classList.toggle("detailed", box.checked);
    try {
      if (box.checked) localStorage.setItem("ctb-detailed", "1");
      else localStorage.removeItem("ctb-detailed");
    } catch (_) { /* storage off: this page only */ }
  });
}

// --- Search: instant results under the header's search box -------------------------------------------------------
// Without this script the box is a plain form that opens /search. Here it becomes a combobox: results from
// /search.json as you type, Up/Down to move, Enter to open, Escape to close; "/" or Ctrl+K jumps to it.
function initSearch() {
  const form = document.querySelector("[data-search]");
  if (!form) return;
  const input = form.querySelector("[data-search-input]");
  const pop = form.querySelector("[data-search-pop]");
  const list = form.querySelector("[data-search-list]");
  const note = form.querySelector("[data-search-note]");
  const live = form.querySelector("[data-search-live]");
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-expanded", "false");
  input.setAttribute("aria-controls", list.id);
  // `stale`: the listed results belong to an older query (typing since), so Enter runs the full search instead.
  let options = [], active = -1, timer = 0, seq = 0, lastQuery = null, stale = false;
  const cache = new Map();

  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  // The name with the typed part marked (text nodes only).
  const marked = (name, query) => {
    const strong = el("strong");
    const at = name.toLowerCase().indexOf(query.toLowerCase());
    if (at < 0 || !query) { strong.textContent = name; return strong; }
    strong.append(name.slice(0, at), el("mark", "", name.slice(at, at + query.length)), name.slice(at + query.length));
    return strong;
  };

  function open(on) {
    pop.hidden = !on;
    input.setAttribute("aria-expanded", String(on));
    if (!on) setActive(-1);
  }

  // Closing (Escape, leaving the box) also drops any lookup still on its way, so it can't reopen the list.
  function dismiss() {
    seq += 1;
    clearTimeout(timer);
    lastQuery = null;
    open(false);
  }

  function setActive(i, scroll = true) {
    active = i;
    options.forEach((opt, k) => opt.setAttribute("aria-selected", String(k === i)));
    if (i >= 0 && options[i]) {
      input.setAttribute("aria-activedescendant", options[i].id);
      if (scroll) options[i].scrollIntoView({ block: "nearest" });
    } else {
      input.removeAttribute("aria-activedescendant");
    }
  }

  function render(data, query) {
    const items = [];
    const option = (href, children) => {
      const li = el("li");
      li.setAttribute("role", "presentation");
      const a = el("a", "search-opt");
      a.href = href;
      a.id = `search-opt-${items.length}`;
      a.setAttribute("role", "option");
      a.setAttribute("aria-selected", "false");
      a.tabIndex = -1;
      a.append(...children);
      li.append(a);
      items.push(a);
      return li;
    };
    const rows = [];
    if (data.players.length) {
      const head = el("li", "search-group", "Players");
      head.setAttribute("role", "presentation");
      rows.push(head);
      data.players.forEach((p) => {
        // A match on the page address alone shows that address, with the typed part marked there.
        const name = marked(p.name, p.via ? "" : query);
        if (p.via) {
          const via = el("span", "opt-via");
          via.append("survev.de: ", marked(p.via, query));
          name.append(via);
        }
        rows.push(option(p.url, [name,
          el("span", "opt-meta", `${p.queues.toLocaleString("en-US")} queue${p.queues === 1 ? "" : "s"}`)]));
      });
    }
    if (data.queues.length) {
      const head = el("li", "search-group", "Queues");
      head.setAttribute("role", "presentation");
      rows.push(head);
      data.queues.forEach((qd) => {
        const parts = [el("strong", "", `#${qd.id}`)];
        if (qd.score) parts.push(el("span", "opt-score", qd.score));
        parts.push(el("span", "opt-meta", qd.ms ? localDate(qd.ms) : qd.date));
        rows.push(option(qd.url, parts));
      });
    }
    list.replaceChildren(...rows);
    options = items;
    note.replaceChildren();
    const all = el("a", "", `All results for "${query}"`);
    all.href = `/search?q=${encodeURIComponent(query)}`;
    if (items.length) {
      note.className = "search-note search-all";
      note.append(all);
    } else {
      note.className = "search-note";
      note.textContent = `No player or queue matches "${query}".`;
    }
    live.textContent = items.length ? `${items.length} result${items.length === 1 ? "" : "s"}` : "No results";
    stale = false;
    setActive(items.length ? 0 : -1);
    open(true);
  }

  async function lookup(query) {
    if (query === lastQuery) { if (query) open(true); return; }
    lastQuery = query;
    if (!query) { list.replaceChildren(); options = []; open(false); return; }
    const mine = ++seq;
    let data = cache.get(query);
    if (!data) {
      try {
        const res = await fetch(`/search.json?q=${encodeURIComponent(query)}`, { headers: { Accept: "application/json" } });
        if (!res.ok) throw new Error(String(res.status));
        data = await res.json();
        cache.set(query, data);
      } catch (_) {
        if (mine !== seq) return;
        list.replaceChildren();
        options = [];
        note.className = "search-note";
        note.textContent = "Search isn't answering right now. Press Enter to try the full search.";
        open(true);
        lastQuery = null;
        return;
      }
    }
    if (mine === seq) render(data, query);
  }

  const query = () => input.value.replace(/\s+/g, " ").trim().slice(0, 40);
  input.addEventListener("input", () => {
    clearTimeout(timer);
    seq += 1; // a lookup for the old text no longer counts
    stale = true;
    setActive(-1);
    timer = setTimeout(() => lookup(query()), 140);
  });
  input.addEventListener("focus", () => { if (query() && options.length && !stale) open(true); });
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (pop.hidden) { if (query()) lookup(query()); return; }
      if (!options.length) return;
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((active + step + options.length) % options.length);
    } else if (event.key === "Enter" && !pop.hidden && !stale && active >= 0 && options[active]) {
      event.preventDefault();
      window.location.href = options[active].href;
    } else if (event.key === "Escape") {
      if (!pop.hidden) { event.preventDefault(); dismiss(); }
      else if (input.value) { input.value = ""; lastQuery = null; stale = true; }
      else input.blur();
    }
  });
  form.addEventListener("submit", (event) => { if (!query()) { event.preventDefault(); input.focus(); } });
  form.addEventListener("focusout", (event) => { if (!form.contains(event.relatedTarget)) dismiss(); });
  list.addEventListener("pointermove", (event) => {
    const opt = event.target.closest(".search-opt");
    if (opt) setActive(options.indexOf(opt), false);
  });
  // "/" (outside a text field) or Ctrl/Cmd+K jumps to the search box.
  document.addEventListener("keydown", (event) => {
    const typing = event.target.closest && event.target.closest("input, textarea, select, [contenteditable='true']");
    if ((event.key === "/" && !typing && !event.ctrlKey && !event.metaKey && !event.altKey)
        || (event.key.toLowerCase() === "k" && (event.ctrlKey || event.metaKey))) {
      event.preventDefault();
      input.focus();
      input.select();
    }
  });
}

// --- Home: the newest queue's score counts up, then the winners' wash sweeps in ----------------------------------
function initFeatured() {
  const band = document.querySelector("[data-featured]");
  if (!band || reduced) return;
  const digits = [...band.querySelectorAll("[data-count-digit]")];
  const win = band.querySelector(".f-team-win");
  onceVisible(band, () => {
    digits.forEach((d, i) => {
      const target = Number(d.dataset.countDigit);
      const state = { v: 0 };
      d.textContent = "0";
      animate(state, {
        v: target, duration: 520, delay: 120 + i * 80, ease: EASE_OUT,
        onUpdate: () => { d.textContent = String(Math.round(state.v)); },
        onComplete: () => { d.textContent = String(target); },
      });
    });
    if (win) {
      win.classList.add("is-sweeping");
      win.addEventListener("animationend", () => win.classList.remove("is-sweeping"), { once: true });
    }
  });
}

initPageTransitions();
initLocalTimes();
initDayHeadings();
initHeader();
initDetail();
initSearch();
initCopy();
initQueueKeys();
initFeatured();
initTimeline();
initGames();
initBoards();
initTrend();
initConsole();
