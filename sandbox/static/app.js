/*
 * Context Menu Lab — browser renderer for the shared Python controller.
 *
 * This file renders MenuView objects and normalizes pointer/keyboard input
 * into MenuEvent records. It deliberately owns no menu state: phase, ordering,
 * availability, arming, search, repeat, and launch decisions all happen in
 * max2025_menu_prototype.core.controller.
 */
(() => {
  'use strict';

  const CONTEXTS = ['object', 'vertex', 'edge', 'border', 'polygon', 'element'];
  const TOKEN = document.querySelector('meta[name="sandbox-token"]')?.content || '';

  const state = {
    view: null,
    profile: null,
    profileJson: '',
    editProfile: null,
    profileDirty: false,
    actions: {},
    snapshot: null,
    scenarios: [],
    contextScenarios: {},
    scenario: null,
    origin: { x: 0, y: 0 },
    triggerSource: null,
    pointerId: null,
    holding: false,
    keyboardHolding: false,
    menuSignature: '',
    lastHighlight: null,
    lastPointerKey: '',
    lastPointerAt: 0,
    profileContext: 'object',
    wasOpen: false,
    queue: Promise.resolve(),
    booted: false,
  };

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => Array.from(document.querySelectorAll(selector));

  class ApiError extends Error {
    constructor(message, status, data) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
      this.data = data || {};
    }
  }

  async function api(path, body) {
    const options = {
      method: body === undefined ? 'GET' : 'POST',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { 'Accept': 'application/json' },
    };
    if (body !== undefined) {
      options.headers['Content-Type'] = 'application/json';
      options.headers['X-Sandbox-Token'] = TOKEN;
      options.body = JSON.stringify(body);
    }
    const response = await fetch(path, options);
    let data = {};
    try {
      data = await response.json();
    } catch (error) {
      data = {};
    }
    if (!response.ok) {
      throw new ApiError(data.error || `${response.status} ${response.statusText}`, response.status, data);
    }
    return data;
  }

  function enqueue(task) {
    state.queue = state.queue.then(task).catch((error) => {
      reportError(error);
    });
    return state.queue;
  }

  let toastTimer = null;
  function showToast(message, info = false) {
    const toast = $('#toast');
    toast.textContent = message;
    toast.classList.toggle('is-info', info);
    toast.hidden = false;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toast.hidden = true; }, 5200);
  }

  function reportError(error) {
    console.error(error);
    showToast(error instanceof ApiError ? error.message : `Request failed: ${error.message || error}`);
  }

  function deepCopy(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function nowMs() {
    return performance.now();
  }

  function kindName(value) {
    if (typeof value === 'string') return value;
    if (value && typeof value === 'object') return value.value || value.name || value.level || '—';
    return '—';
  }

  function shortSession(value) {
    if (!value) return '—';
    return String(value).length > 14 ? `${String(value).slice(0, 12)}…` : String(value);
  }

  function actionMeta(id) {
    return state.actions[id] || {};
  }

  function actionLabel(id) {
    return actionMeta(id).label || id;
  }

  function prettyName(value) {
    return String(value || '').replace(/[_-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  }

  /* ------------------------------------------------------------------ */
  /* API state                                                           */
  /* ------------------------------------------------------------------ */

  function applyState(data) {
    if (!data || !data.view) return;
    state.view = data.view;
    if (data.actions) state.actions = data.actions;
    if (data.snapshot) state.snapshot = data.snapshot;
    if (data.scenario !== undefined) state.scenario = data.scenario;
    if (Array.isArray(data.scenarios)) state.scenarios = data.scenarios;
    if (data.context_scenarios && typeof data.context_scenarios === 'object') {
      state.contextScenarios = data.context_scenarios;
    }

    if (data.profile) {
      const incoming = JSON.stringify(data.profile);
      state.profile = data.profile;
      if (incoming !== state.profileJson && !state.profileDirty) {
        state.editProfile = deepCopy(data.profile);
        state.profileJson = incoming;
        syncProfileInputs();
        renderProfileList();
      } else {
        state.profileJson = incoming;
      }
    }

    const isOpen = state.view.phase !== 'closed';
    if (state.wasOpen && !isOpen) {
      restoreFocusAfterClose();
      state.holding = false;
      state.keyboardHolding = false;
      state.triggerSource = null;
      hideNeutral();
    }
    state.wasOpen = isOpen;
    $('.viewport')?.classList.toggle('is-engaged', isOpen);

    renderMenu(state.view);
    renderState(state.view);
    renderScenarioControls();
  }

  function restoreFocusAfterClose() {
    const active = document.activeElement;
    const insideMenu = active && active.closest && active.closest('#menu-layer');
    if (!active || active === document.body || insideMenu) {
      $('#viewport')?.focus({ preventScroll: true });
    }
  }

  async function refreshState() {
    const data = await api('/api/state');
    applyState(data);
    if (!state.booted) {
      state.booted = true;
      setConnection('ok', 'live · loopback');
      syncProfileInputs();
      renderProfileList();
      drawViewport();
    }
  }

  function setConnection(kind, text) {
    const badge = $('#connection');
    badge.className = `badge badge-${kind}`;
    badge.textContent = text;
  }

  /* ------------------------------------------------------------------ */
  /* Menu rendering                                                      */
  /* ------------------------------------------------------------------ */

  function menuSignature(view) {
    const rows = (view.rows || []).map((row) => [
      row.action_id, row.label, row.group, !!row.enabled, row.reason || '',
      row.execution_kind || '',
    ]);
    return JSON.stringify([
      view.session_id, kindName(view.phase), kindName(view.context), view.query || '', rows,
    ]);
  }

  function phaseText(view) {
    const phase = kindName(view.phase);
    if (phase === 'held') return 'held · release after threshold';
    if (phase === 'latched') return 'latched · click or Enter';
    if (phase === 'search') return 'search · type or Enter';
    return phase;
  }

  function hintText(view) {
    const phase = kindName(view.phase);
    if (phase === 'held') return 'Move onto a row and release to run · early release latches';
    if (phase === 'search') return 'Backspace edits · Esc cancels';
    if (phase === 'latched') return '↑↓ move · Enter runs · Esc cancels';
    return 'Esc cancels';
  }

  function renderMenu(view) {
    const layer = $('#menu-layer');
    if (!layer) return;
    if (kindName(view.phase) === 'closed') {
      layer.replaceChildren();
      state.menuSignature = '';
      state.lastHighlight = null;
      return;
    }

    const signature = menuSignature(view);
    let popup = layer.querySelector('.menu-popup');
    if (!popup || signature !== state.menuSignature) {
      const scrollTop = popup?.querySelector('.menu-rows')?.scrollTop || 0;
      popup = buildMenuPopup(view);
      layer.replaceChildren(popup);
      const rows = popup.querySelector('.menu-rows');
      if (rows && scrollTop) rows.scrollTop = scrollTop;
      state.menuSignature = signature;
      placeMenu(popup);
    } else {
      updateMenuPopup(popup, view);
    }
    syncHighlight(popup, view);
  }

  function buildMenuPopup(view) {
    const popup = document.createElement('div');
    popup.className = 'menu-popup';
    popup.setAttribute('role', 'menu');
    popup.setAttribute('aria-label', 'Context menu');

    const head = document.createElement('div');
    head.className = 'menu-head';
    const context = document.createElement('span');
    context.className = 'menu-context';
    context.textContent = `${kindName(view.context)} context`;
    const phase = document.createElement('span');
    phase.className = 'menu-phase';
    phase.textContent = shortSession(view.session_id);
    head.append(context, phase);

    const search = document.createElement('div');
    search.className = 'menu-search';
    const searchText = document.createElement('span');
    searchText.className = 'search-text';
    search.append(searchText, Object.assign(document.createElement('span'), { className: 'caret' }));

    const rows = document.createElement('div');
    rows.className = 'menu-rows';
    rows.setAttribute('role', 'group');
    rows.append(...buildRows(view));

    const foot = document.createElement('div');
    foot.className = 'menu-foot';
    const status = document.createElement('span');
    status.className = 'menu-status';
    const hint = document.createElement('span');
    hint.className = 'menu-hint';
    hint.textContent = hintText(view);
    foot.append(status, hint);

    popup.append(head, search, rows, foot);
    updateMenuPopup(popup, view);
    return popup;
  }

  function buildRows(view) {
    const fragment = [];
    const rows = view.rows || [];
    if (!rows.length) {
      const empty = document.createElement('div');
      empty.className = 'menu-empty';
      const strong = document.createElement('strong');
      const detail = document.createElement('span');
      if ((view.query || '').trim()) {
        strong.textContent = 'No matching actions';
        detail.textContent = 'Enter does nothing: the filtered list is empty. Clear the search with Backspace.';
      } else {
        strong.textContent = 'No actions in this profile';
        detail.textContent = 'Show actions in the profile editor, then reopen the menu.';
      }
      empty.append(strong, detail);
      fragment.push(empty);
      return fragment;
    }

    let previousGroup = null;
    for (const row of rows) {
      const group = row.group || 'Actions';
      if (group !== previousGroup) {
        const heading = document.createElement('div');
        heading.className = 'menu-group-label';
        heading.textContent = prettyName(group);
        fragment.push(heading);
        previousGroup = group;
      }

      const button = document.createElement('button');
      button.type = 'button';
      button.className = `menu-row${row.enabled ? '' : ' is-disabled'}`;
      button.dataset.actionId = row.action_id;
      button.setAttribute('role', 'menuitem');
      button.setAttribute('aria-disabled', row.enabled ? 'false' : 'true');
      button.title = row.reason || row.label;

      const dot = document.createElement('span');
      dot.className = 'row-dot';
      const text = document.createElement('span');
      text.className = 'row-text';
      const label = document.createElement('span');
      label.className = 'row-label';
      label.textContent = row.label;
      text.append(label);
      if (!row.enabled && row.reason) {
        const reason = document.createElement('span');
        reason.className = 'row-reason';
        reason.textContent = row.reason;
        text.append(reason);
      }
      const meta = document.createElement('span');
      meta.className = 'row-meta';
      meta.textContent = row.action_id;
      text.append(meta);

      const kind = document.createElement('span');
      kind.className = 'row-kind';
      kind.textContent = row.execution_kind || 'action';

      button.append(dot, text, kind);
      fragment.push(button);
    }
    return fragment;
  }

  function updateMenuPopup(popup, view) {
    const context = popup.querySelector('.menu-context');
    if (context) context.textContent = `${kindName(view.context)} context`;
    const phase = popup.querySelector('.menu-phase');
    if (phase) phase.textContent = phaseText(view);
    const search = popup.querySelector('.menu-search');
    const searchText = popup.querySelector('.search-text');
    if (searchText && search) {
      const query = view.query || '';
      searchText.textContent = query || 'Type to search actions';
      search.classList.toggle('is-placeholder', !query);
    }
    const status = popup.querySelector('.menu-status');
    if (status) {
      status.textContent = view.status || 'Ready';
      status.classList.toggle('is-error', /error|blocked|invalid|cancel/i.test(view.status || ''));
    }
    const hint = popup.querySelector('.menu-hint');
    if (hint) hint.textContent = hintText(view);
    popup.dataset.phase = kindName(view.phase);
  }

  function syncHighlight(popup, view) {
    const rows = popup.querySelectorAll('.menu-row');
    const highlighted = view.highlighted_id || null;
    const armed = view.armed_id || null;
    for (const row of rows) {
      row.classList.toggle('is-highlighted', !!highlighted && row.dataset.actionId === highlighted);
      row.classList.toggle('is-armed', !!armed && row.dataset.actionId === armed);
    }
    if (highlighted && highlighted !== state.lastHighlight) {
      const container = popup.querySelector('.menu-rows');
      const row = Array.from(rows).find((item) => item.dataset.actionId === highlighted);
      if (container && row) {
        const top = row.offsetTop;
        const bottom = top + row.offsetHeight;
        if (top < container.scrollTop) container.scrollTop = Math.max(0, top - 4);
        else if (bottom > container.scrollTop + container.clientHeight) {
          container.scrollTop = bottom - container.clientHeight + 4;
        }
      }
    }
    state.lastHighlight = highlighted;
  }

  function placeMenu(popup) {
    const viewport = $('#viewport');
    if (!viewport) return;
    const width = popup.offsetWidth || 338;
    const height = popup.offsetHeight || 320;
    const bounds = viewport.getBoundingClientRect();
    const origin = state.origin;
    let left = origin.x + 18;
    let top = origin.y + 16;
    if (left + width > bounds.width - 8) left = origin.x - width - 18;
    if (top + height > bounds.height - 8) top = origin.y - height - 16;
    left = Math.max(8, Math.min(left, Math.max(8, bounds.width - width - 8)));
    top = Math.max(8, Math.min(top, Math.max(8, bounds.height - height - 8)));
    popup.style.left = `${Math.round(left)}px`;
    popup.style.top = `${Math.round(top)}px`;
  }

  /* ------------------------------------------------------------------ */
  /* Inspector and log                                                   */
  /* ------------------------------------------------------------------ */

  function renderState(view) {
    const phase = kindName(view.phase);
    const context = kindName(view.context);
    const snapshot = state.snapshot || {};

    $('#state-context').textContent = context;
    $('#state-phase').textContent = phase;
    $('#state-session').textContent = shortSession(view.session_id);
    $('#state-sequence').textContent = String(view.sequence ?? 0);
    $('#state-query').textContent = view.query ? `“${view.query}”` : '—';
    $('#state-highlighted').textContent = view.highlighted_id || '—';
    $('#state-armed').textContent = view.armed_id || '—';
    $('#state-status').textContent = view.status || '—';

    $('#snap-kind').textContent = snapshot.target_kind || '—';
    $('#snap-level').textContent = kindName(snapshot.level);
    $('#snap-count').textContent = snapshot.selection_count ?? '—';
    $('#snap-panel').textContent = snapshot.panel_ready === undefined ? '—' : (snapshot.panel_ready ? 'yes' : 'no');
    $('#snap-conflict').textContent = snapshot.conflicting_mode || '—';
    $('#snap-revision').textContent = snapshot.revision ?? '—';

    $('#hud-context').textContent = context === '—' ? 'no context' : context;
    $('#hud-phase').textContent = phase;

    renderLastResult(view.last_result);
    renderLog(view.log || []);
  }

  function renderLastResult(result) {
    const target = $('#last-result');
    if (!result) {
      target.textContent = 'No launch yet.';
      return;
    }
    const status = result.status || 'unknown';
    const action = result.action_id || '—';
    const message = result.message || '';
    target.textContent = `${status.toUpperCase()} · ${action}${message ? ` — ${message}` : ''}`;
  }

  function renderLog(entries) {
    const list = $('#action-log');
    list.replaceChildren();
    if (!entries.length) {
      const empty = document.createElement('li');
      empty.className = 'log-empty';
      empty.textContent = 'No accepted actions yet. Open the menu and choose a row.';
      list.append(empty);
      return;
    }
    const bounded = entries.slice(-50).reverse();
    for (const entry of bounded) {
      const item = document.createElement('li');
      item.className = 'log-entry';
      const time = document.createElement('span');
      time.className = 'log-time';
      const stamp = entry.time_ms ?? entry.time ?? entry.at_ms;
      time.textContent = stamp === undefined ? '—' : `${Math.round(Number(stamp))}ms`;
      const main = document.createElement('span');
      main.className = 'log-main';
      const action = document.createElement('span');
      action.className = 'log-action';
      action.textContent = entry.action_id || entry.action || entry.kind || entry.event || 'event';
      const detail = document.createElement('span');
      detail.className = 'log-detail';
      const status = entry.status || entry.result || '';
      const parts = [kindName(entry.context), status, shortSession(entry.session_id || entry.session)]
        .filter((part) => part && part !== '—' && part !== '[object Object]');
      detail.textContent = parts.join(' · ') || 'recorded';
      if (status) detail.classList.add(`log-status-${String(status)}`);
      main.append(action, detail);
      item.append(time, main);
      list.append(item);
    }
  }

  /* ------------------------------------------------------------------ */
  /* Event plumbing                                                      */
  /* ------------------------------------------------------------------ */

  function localPoint(clientX, clientY) {
    const bounds = $('#viewport').getBoundingClientRect();
    return { x: clientX - bounds.left, y: clientY - bounds.top };
  }

  function relativePoint(local) {
    return {
      x: Math.round(local.x - state.origin.x),
      y: Math.round(local.y - state.origin.y),
    };
  }

  function neutralRadius() {
    const value = Number(state.profile?.neutral_px ?? 12);
    return Number.isFinite(value) ? value : 12;
  }

  async function openMenu(invocation, timeMs, origin) {
    state.origin = origin;
    const data = await api('/api/open', {
      invocation,
      time_ms: Math.max(0, Math.round(timeMs)),
    });
    applyState(data);
  }

  async function postEventDirect(kind, fields = {}) {
    const view = state.view;
    if (!view || kindName(view.phase) === 'closed') return null;
    const sequence = Number(view.sequence || 0) + 1;
    const time = fields.__time === undefined ? nowMs() : fields.__time;
    const event = {
      session_id: view.session_id,
      sequence,
      kind,
      time_ms: Math.max(0, Math.round(time)),
      x: Number.isFinite(fields.x) ? Math.round(fields.x) : 0,
      y: Number.isFinite(fields.y) ? Math.round(fields.y) : 0,
      action_id: fields.action_id || null,
      text: fields.text || '',
      key: fields.key || '',
      auto_repeat: !!fields.auto_repeat,
    };
    const data = await api('/api/event', { event });
    applyState(data);
    return data;
  }

  function sendEvent(kind, fields = {}, timeMs = nowMs()) {
    return enqueue(() => postEventDirect(kind, { ...fields, __time: timeMs }));
  }

  function sendOpen(invocation, timeMs, origin) {
    return enqueue(() => openMenu(invocation, timeMs, origin));
  }

  function hitTestRow(clientX, clientY) {
    const element = document.elementFromPoint(clientX, clientY);
    return element?.closest?.('.menu-row') || null;
  }

  function showNeutral(point) {
    const zone = $('#neutral-zone');
    if (!zone) return;
    const radius = neutralRadius();
    const diameter = Math.max(10, radius * 2);
    zone.style.width = `${diameter}px`;
    zone.style.height = `${diameter}px`;
    zone.style.margin = `${-diameter / 2}px 0 0 ${-diameter / 2}px`;
    zone.style.left = `${point.x}px`;
    zone.style.top = `${point.y}px`;
    zone.hidden = false;
  }

  function hideNeutral() {
    const zone = $('#neutral-zone');
    if (zone) zone.hidden = true;
  }

  function pointerMove(clientX, clientY, force = false) {
    const view = state.view;
    if (!view || kindName(view.phase) === 'closed') return;
    if (state.triggerSource === 'keyboard') return;

    const local = localPoint(clientX, clientY);
    const relative = relativePoint(local);
    if (local.x < -40 || local.y < -40) return;
    const distance = Math.hypot(relative.x, relative.y);
    const row = hitTestRow(clientX, clientY);
    const actionId = row?.dataset?.actionId || null;
    const zone = distance <= neutralRadius() ? 'neutral' : 'outside';
    const key = `${actionId || '-'}:${zone}`;
    const stamp = nowMs();
    if (!force && key === state.lastPointerKey && stamp - state.lastPointerAt < 70) return;
    state.lastPointerKey = key;
    state.lastPointerAt = stamp;
    sendEvent('pointer_move', {
      x: relative.x,
      y: relative.y,
      action_id: actionId,
    }, stamp);
  }

  /* ------------------------------------------------------------------ */
  /* Pointer gestures                                                    */
  /* ------------------------------------------------------------------ */

  function beginPointerTrigger(event) {
    const viewport = $('#viewport');
    const local = localPoint(event.clientX, event.clientY);
    state.origin = local;
    state.triggerSource = 'pointer';
    state.holding = true;
    state.pointerId = event.pointerId;
    state.lastPointerKey = '';
    showNeutral(local);
    viewport.focus({ preventScroll: true });
    sendOpen('held', nowMs(), local);
  }

  function bindPointer() {
    const viewport = $('#viewport');

    viewport.addEventListener('pointerdown', (event) => {
      if (event.button !== 0) return;
      if (event.target.closest?.('.menu-popup')) return;
      const open = state.view && kindName(state.view.phase) !== 'closed';
      if (open) {
        // The capture-phase outside-click handler sends the cancellation.
        event.preventDefault();
        return;
      }
      event.preventDefault();
      beginPointerTrigger(event);
    });

    window.addEventListener('pointermove', (event) => {
      if (!state.holding && !(state.view && kindName(state.view.phase) !== 'closed')) return;
      pointerMove(event.clientX, event.clientY);
    }, { passive: true });

    window.addEventListener('pointerup', (event) => {
      if (!state.holding || event.pointerId !== state.pointerId) return;
      state.holding = false;
      state.pointerId = null;
      state.triggerSource = null;
      const local = localPoint(event.clientX, event.clientY);
      const relative = relativePoint(local);
      hideNeutral();
      sendEvent('trigger_up', { x: relative.x, y: relative.y }, nowMs());
    });

    window.addEventListener('pointercancel', () => {
      if (!state.holding && !state.keyboardHolding) return;
      state.holding = false;
      state.keyboardHolding = false;
      state.triggerSource = null;
      hideNeutral();
      sendEvent('cancel', { key: 'PointerCancel' }, nowMs());
    });
  }

  /* ------------------------------------------------------------------ */
  /* Keyboard gestures                                                   */
  /* ------------------------------------------------------------------ */

  function isEditable(target) {
    if (!target || !target.closest) return false;
    return !!target.closest('input, textarea, select, [contenteditable="true"], [contenteditable=""]');
  }

  function keyFields(event) {
    let key = event.key;
    if (key === 'Esc') key = 'Escape';
    if (key === 'Up') key = 'ArrowUp';
    if (key === 'Down') key = 'ArrowDown';
    if (key === 'Left') key = 'ArrowLeft';
    if (key === 'Right') key = 'ArrowRight';
    const text = key.length === 1 ? key : '';
    return { key, text, auto_repeat: !!event.repeat };
  }

  function keyboardOrigin() {
    const viewport = $('#viewport');
    const bounds = viewport.getBoundingClientRect();
    return { x: Math.round(bounds.width / 2), y: Math.round(bounds.height / 2) };
  }

  function bindKeyboard() {
    document.addEventListener('keydown', (event) => {
      const editable = isEditable(event.target);
      const open = state.view && kindName(state.view.phase) !== 'closed';

      if (!open) {
        if (event.code === 'Space' && !editable && document.activeElement === $('#viewport')) {
          event.preventDefault();
          if (event.repeat) return;
          const origin = keyboardOrigin();
          state.origin = origin;
          state.triggerSource = 'keyboard';
          state.keyboardHolding = true;
          showNeutral(origin);
          sendOpen('held', nowMs(), origin);
        }
        return;
      }

      if (editable) return;

      if (event.key === 'Escape') {
        event.preventDefault();
        state.holding = false;
        state.keyboardHolding = false;
        state.triggerSource = null;
        sendEvent('cancel', { key: 'Escape' }, nowMs());
        return;
      }

      if (event.key === 'ArrowUp' || event.key === 'ArrowDown' || event.key === 'Enter' ||
          event.key === 'Backspace' || event.key === 'Tab') {
        if (event.key === 'Tab') return;
        event.preventDefault();
        const fields = keyFields(event);
        sendEvent('key_down', fields, nowMs());
        return;
      }

      if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault();
        sendEvent('key_down', { ...keyFields(event), text: event.key }, nowMs());
      }
    });

    document.addEventListener('keyup', (event) => {
      if (event.code !== 'Space' || !state.keyboardHolding) return;
      event.preventDefault();
      state.keyboardHolding = false;
      state.triggerSource = null;
      hideNeutral();
      sendEvent('trigger_up', { x: 0, y: 0 }, nowMs());
    });
  }

  /* ------------------------------------------------------------------ */
  /* Cancellation                                                        */
  /* ------------------------------------------------------------------ */

  function bindCancellation() {
    document.addEventListener('pointerdown', (event) => {
      if (!state.view || kindName(state.view.phase) === 'closed') return;
      if (event.target.closest?.('.menu-popup')) return;
      const local = localPoint(event.clientX, event.clientY);
      const relative = relativePoint(local);
      state.holding = false;
      state.keyboardHolding = false;
      state.triggerSource = null;
      hideNeutral();
      sendEvent('outside_click', { x: relative.x, y: relative.y }, nowMs());
    }, true);

    window.addEventListener('blur', () => {
      if (!state.view || kindName(state.view.phase) === 'closed') return;
      state.holding = false;
      state.keyboardHolding = false;
      state.triggerSource = null;
      hideNeutral();
      sendEvent('focus_lost', {}, nowMs());
    });

    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) return;
      if (!state.view || kindName(state.view.phase) === 'closed') return;
      state.holding = false;
      state.keyboardHolding = false;
      state.triggerSource = null;
      hideNeutral();
      sendEvent('focus_lost', {}, nowMs());
    });
  }

  /* ------------------------------------------------------------------ */
  /* Menu row input                                                      */
  /* ------------------------------------------------------------------ */

  function bindMenu() {
    $('#menu-layer').addEventListener('click', (event) => {
      const row = event.target.closest?.('.menu-row');
      if (!row) return;
      event.preventDefault();
      sendEvent('click_row', { action_id: row.dataset.actionId }, nowMs());
    });
  }

  /* ------------------------------------------------------------------ */
  /* Guided alternatives                                                 */
  /* ------------------------------------------------------------------ */

  function ensureOpenLatched() {
    const open = state.view && kindName(state.view.phase) !== 'closed';
    if (open) return Promise.resolve();
    return openMenu('latched', nowMs(), { x: 80, y: 70 });
  }

  function guidedTap() {
    const origin = { x: 90, y: 80 };
    const start = nowMs();
    enqueue(async () => {
      await openMenu('held', start, origin);
      await postEventDirect('trigger_up', { x: 0, y: 0, __time: start + 70 });
    });
  }

  function guidedHoldNeutral() {
    const origin = { x: 90, y: 80 };
    const start = nowMs();
    enqueue(async () => {
      await openMenu('held', start, origin);
      await postEventDirect('pointer_move', { x: 0, y: 0, __time: start + 300 });
      await postEventDirect('trigger_up', { x: 0, y: 0, __time: start + 380 });
    });
  }

  function guidedLatched() {
    enqueue(() => openMenu('latched', nowMs(), { x: 90, y: 80 }));
  }

  function guidedDisabled() {
    enqueue(async () => {
      await ensureOpenLatched();
      const rows = state.view?.rows || [];
      const row = rows.find((item) => !item.enabled) || rows.find((item) => item.action_id === 'menu.repeat');
      if (!row) {
        showToast('This scenario has no unavailable row to inspect.', true);
        return;
      }
      await postEventDirect('click_row', { action_id: row.action_id });
    });
  }

  function guidedBreakContext() {
    const stale = state.scenarios.map(normalizeScenario).find(
      (item) => /(changed|deleted|stale|invalid)/i.test(item.name) || /(changed|deleted|stale|invalid)/i.test(item.label)
    );
    if (!stale) {
      showToast('No stale-target scenario is available in this build.', true);
      return;
    }
    enqueue(async () => {
      await ensureOpenLatched();
      const previous = state.view?.rows || [];
      const target = previous.find((item) => item.enabled) || previous[0];
      const data = await api('/api/scenario', { name: stale.name });
      applyState(data);
      if (state.view && kindName(state.view.phase) !== 'closed' && target) {
        // Revalidation happens at commit time in the shared controller; this
        // proves the changed target blocks instead of silently repairing it.
        await postEventDirect('click_row', { action_id: target.action_id });
      }
    });
  }

  function guidedRepeat() {
    enqueue(async () => {
      await ensureOpenLatched();
      const rows = state.view?.rows || [];
      const row = rows.find((item) => item.action_id === 'menu.repeat');
      if (!row) {
        showToast('The repeat row is not visible in this profile.', true);
        return;
      }
      await postEventDirect('click_row', { action_id: 'menu.repeat' });
    });
  }

  function guidedEscape() {
    if (!state.view || kindName(state.view.phase) === 'closed') {
      showToast('The menu is already closed.', true);
      return;
    }
    sendEvent('cancel', { key: 'Escape' }, nowMs());
  }

  function bindGuided() {
    $$('[data-guide]').forEach((button) => {
      button.addEventListener('click', () => {
        switch (button.dataset.guide) {
          case 'tap': guidedTap(); break;
          case 'hold-neutral': guidedHoldNeutral(); break;
          case 'latched': guidedLatched(); break;
          case 'disabled': guidedDisabled(); break;
          case 'break-context': guidedBreakContext(); break;
          case 'repeat': guidedRepeat(); break;
          case 'escape': guidedEscape(); break;
          default: break;
        }
      });
    });
  }

  /* ------------------------------------------------------------------ */
  /* Scenario controls                                                   */
  /* ------------------------------------------------------------------ */

  function normalizeScenario(value) {
    if (typeof value === 'string') return { name: value, label: prettyName(value), level: null };
    if (value && typeof value === 'object') {
      return {
        name: value.name || value.id || '',
        label: value.label || prettyName(value.name || value.id || ''),
        level: value.level || value.context || null,
      };
    }
    return { name: '', label: '—', level: null };
  }

  function chooseScenario(name) {
    if (!name) return;
    enqueue(async () => {
      const data = await api('/api/scenario', { name });
      applyState(data);
    });
  }

  function renderScenarioControls() {
    const select = $('#scenario-select');
    if (select && state.scenarios.length) {
      const current = state.scenario;
      const signature = JSON.stringify(state.scenarios);
      if (select.dataset.signature !== signature) {
        select.replaceChildren();
        for (const raw of state.scenarios) {
          const item = normalizeScenario(raw);
          const option = document.createElement('option');
          option.value = item.name;
          option.textContent = item.label;
          select.append(option);
        }
        select.dataset.signature = signature;
      }
      if (current && select.value !== current) select.value = current;
    }

    const label = $('#scenario-label');
    if (label) {
      const current = state.scenarios.map(normalizeScenario).find((item) => item.name === state.scenario);
      label.textContent = current ? current.label : 'simulated host';
    }

    const holder = $('#context-buttons');
    if (!holder) return;
    const currentScenario = state.scenarios
      .map(normalizeScenario)
      .find((item) => item.name === state.scenario);
    const openContext = state.view && kindName(state.view.phase) !== 'closed'
      ? kindName(state.view.context)
      : '';
    const active = openContext
      || kindName(state.snapshot?.level || '')
      || currentScenario?.level
      || '';
    const signature = JSON.stringify([
      state.contextScenarios,
      state.scenarios,
      state.scenario,
      kindName(state.snapshot?.level || ''),
      active,
    ]);
    if (holder.dataset.signature === signature) return;
    holder.dataset.signature = signature;
    holder.replaceChildren();
    for (const context of CONTEXTS) {
      const mapped = state.contextScenarios?.[context];
      const fromScenario = state.scenarios.map(normalizeScenario).find((item) => item.level === context)?.name;
      const name = mapped || fromScenario;
      const button = document.createElement('button');
      button.type = 'button';
      button.className = `context-btn${active === context ? ' is-active' : ''}`;
      button.textContent = context;
      if (!name) {
        button.disabled = true;
        button.title = 'No simulated scenario targets this context yet.';
      } else {
        button.title = `Switch to the ${context} simulation`;
        button.addEventListener('click', () => chooseScenario(name));
      }
      holder.append(button);
    }
  }

  /* ------------------------------------------------------------------ */
  /* Profile editor                                                      */
  /* ------------------------------------------------------------------ */

  function profileContext() {
    if (!state.editProfile?.contexts) return null;
    if (!state.editProfile.contexts[state.profileContext]) {
      state.profileContext = Object.keys(state.editProfile.contexts)[0] || 'object';
    }
    return state.editProfile.contexts[state.profileContext];
  }

  function syncProfileInputs() {
    const profile = state.profile;
    if (!profile) return;
    const tap = $('#tap-ms');
    const neutral = $('#neutral-px');
    if (tap && document.activeElement !== tap) tap.value = String(profile.tap_ms ?? 220);
    if (neutral && document.activeElement !== neutral) neutral.value = String(profile.neutral_px ?? 12);
  }

  function renderProfileList() {
    const select = $('#profile-context');
    if (select && state.editProfile?.contexts) {
      if (!select.options.length) {
        for (const context of CONTEXTS) {
          if (!(context in state.editProfile.contexts)) continue;
          const option = document.createElement('option');
          option.value = context;
          option.textContent = context;
          select.append(option);
        }
      }
      select.value = state.profileContext;
    }

    const list = $('#profile-list');
    if (!list) return;
    list.replaceChildren();
    const entries = profileContext();
    if (!entries) {
      const empty = document.createElement('li');
      empty.className = 'profile-help';
      empty.textContent = 'No profile loaded.';
      list.append(empty);
      return;
    }

    entries.forEach((entry, index) => {
      const item = document.createElement('li');
      item.className = `profile-row${entry.visible === false ? ' is-hidden' : ''}`;

      const visible = document.createElement('input');
      visible.type = 'checkbox';
      visible.checked = entry.visible !== false;
      visible.title = 'Show this action in the menu';
      visible.addEventListener('change', () => {
        entry.visible = visible.checked;
        markProfileDirty();
        renderProfileList();
      });

      const text = document.createElement('span');
      text.className = 'p-id';
      const label = document.createElement('span');
      label.className = 'p-label';
      label.textContent = actionMeta(entry.id).label || actionLabel(entry.id);
      text.append(document.createTextNode(entry.id), document.createElement('br'), label);

      const order = document.createElement('span');
      order.className = 'p-order';
      const up = document.createElement('button');
      up.type = 'button';
      up.textContent = '↑';
      up.title = 'Move earlier';
      up.disabled = index === 0;
      up.addEventListener('click', () => moveProfileEntry(index, -1));
      const down = document.createElement('button');
      down.type = 'button';
      down.textContent = '↓';
      down.title = 'Move later';
      down.disabled = index === entries.length - 1;
      down.addEventListener('click', () => moveProfileEntry(index, 1));
      order.append(up, down);

      item.append(visible, text, order);
      list.append(item);
    });
  }

  function moveProfileEntry(index, delta) {
    const entries = profileContext();
    if (!entries) return;
    const target = index + delta;
    if (target < 0 || target >= entries.length) return;
    const [entry] = entries.splice(index, 1);
    entries.splice(target, 0, entry);
    markProfileDirty();
    renderProfileList();
  }

  function markProfileDirty() {
    state.profileDirty = true;
    setProfileMessage('Unsaved in-memory edits. Apply to send them to the controller.', '');
  }

  function setProfileMessage(message, kind) {
    const target = $('#profile-message');
    if (!target) return;
    target.textContent = message;
    target.className = `profile-message${kind ? ` is-${kind}` : ''}`;
  }

  function editedProfilePayload() {
    if (!state.editProfile) return null;
    const payload = deepCopy(state.editProfile);
    const tap = Number($('#tap-ms').value);
    const neutral = Number($('#neutral-px').value);
    payload.tap_ms = Number.isFinite(tap) ? tap : payload.tap_ms;
    payload.neutral_px = Number.isFinite(neutral) ? neutral : payload.neutral_px;
    return payload;
  }

  function applyProfile() {
    const payload = editedProfilePayload();
    if (!payload) return;
    enqueue(async () => {
      try {
        const data = await api('/api/profile', { action: 'set', profile: payload });
        state.profileDirty = false;
        applyState(data);
        if (data.profile) {
          state.editProfile = deepCopy(data.profile);
          state.profileJson = JSON.stringify(data.profile);
          renderProfileList();
          syncProfileInputs();
        }
        setProfileMessage('Applied in memory. No file was written.', 'ok');
      } catch (error) {
        setProfileMessage(error.data?.error || error.message, 'error');
        throw error;
      }
    });
  }

  function resetProfile() {
    enqueue(async () => {
      try {
        const data = await api('/api/profile', { action: 'reset' });
        state.profileDirty = false;
        applyState(data);
        if (data.profile) {
          state.editProfile = deepCopy(data.profile);
          state.profileJson = JSON.stringify(data.profile);
          state.profileContext = 'object';
          renderProfileList();
          syncProfileInputs();
        }
        setProfileMessage('Restored the shipped default profile.', 'ok');
      } catch (error) {
        setProfileMessage(error.data?.error || error.message, 'error');
        throw error;
      }
    });
  }

  function exportProfile() {
    const textarea = $('#profile-json');
    const payload = editedProfilePayload();
    if (textarea && payload) {
      textarea.value = JSON.stringify(payload, null, 2);
      setProfileMessage('Exported to the textarea. Copy or download it explicitly.', 'ok');
    }
  }

  async function copyProfile() {
    const textarea = $('#profile-json');
    if (!textarea.value) exportProfile();
    try {
      await navigator.clipboard.writeText(textarea.value);
      setProfileMessage('Profile JSON copied to the clipboard.', 'ok');
    } catch (error) {
      textarea.select();
      setProfileMessage('Clipboard blocked; the JSON is selected for manual copy.', '');
    }
  }

  function downloadProfile() {
    const textarea = $('#profile-json');
    if (!textarea.value) exportProfile();
    const blob = new Blob([textarea.value], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'context-menu-profile.json';
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setProfileMessage('Downloaded an explicit profile export.', 'ok');
  }

  function importProfile() {
    const textarea = $('#profile-json');
    const text = textarea?.value || '';
    if (!text.trim()) {
      setProfileMessage('Paste a profile JSON document before importing.', 'error');
      return;
    }
    enqueue(async () => {
      try {
        const data = await api('/api/profile', { action: 'import', text });
        state.profileDirty = false;
        applyState(data);
        if (data.profile) {
          state.editProfile = deepCopy(data.profile);
          state.profileJson = JSON.stringify(data.profile);
          renderProfileList();
          syncProfileInputs();
          textarea.value = JSON.stringify(data.profile, null, 2);
        }
        setProfileMessage('Imported and applied. The previous profile was replaced only after validation.', 'ok');
      } catch (error) {
        setProfileMessage(error.data?.error || error.message, 'error');
        throw error;
      }
    });
  }

  function bindProfile() {
    $('#profile-context')?.addEventListener('change', (event) => {
      state.profileContext = event.target.value;
      renderProfileList();
    });
    $('#profile-apply')?.addEventListener('click', applyProfile);
    $('#profile-reset')?.addEventListener('click', resetProfile);
    $('#profile-export')?.addEventListener('click', exportProfile);
    $('#profile-copy')?.addEventListener('click', copyProfile);
    $('#profile-download')?.addEventListener('click', downloadProfile);
    $('#profile-import')?.addEventListener('click', importProfile);
    ['#tap-ms', '#neutral-px'].forEach((selector) => {
      $(selector)?.addEventListener('input', () => {
        state.profileDirty = true;
        setProfileMessage('Threshold changes are pending. Apply in memory.', '');
      });
    });
  }

  /* ------------------------------------------------------------------ */
  /* Decorative viewport                                                 */
  /* ------------------------------------------------------------------ */

  function drawViewport() {
    const viewport = $('#viewport');
    const canvas = $('#viewport-canvas');
    if (!viewport || !canvas) return;
    const width = viewport.clientWidth;
    const height = viewport.clientHeight;
    if (!width || !height) return;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);

    // Backdrop.
    const gradient = ctx.createLinearGradient(0, 0, 0, height);
    gradient.addColorStop(0, '#0b0e13');
    gradient.addColorStop(1, '#080a0e');
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, width, height);

    // Grid.
    const step = 38;
    ctx.lineWidth = 1;
    for (let x = (width / 2) % step; x < width; x += step) {
      ctx.strokeStyle = x === Math.round(width / 2) ? 'rgba(103,184,255,0.18)' : 'rgba(255,255,255,0.035)';
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, height); ctx.stroke();
    }
    for (let y = (height / 2) % step; y < height; y += step) {
      ctx.strokeStyle = y === Math.round(height / 2) ? 'rgba(103,184,255,0.18)' : 'rgba(255,255,255,0.035)';
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(width, y); ctx.stroke();
    }

    // Object.
    const cx = width * 0.5;
    const cy = height * 0.56;
    const scale = Math.min(width, height) * 0.21;
    const project = (x, y, z) => [
      cx + (x - y) * scale * 0.92,
      cy + (x + y) * scale * 0.46 - z * scale,
    ];
    const corners = {
      a: project(-1, -1, 1), b: project(1, -1, 1), c: project(1, 1, 1), d: project(-1, 1, 1),
      e: project(-1, -1, 0), f: project(1, -1, 0), g: project(1, 1, 0), h: project(-1, 1, 0),
    };
    const faces = [
      ['a', 'b', 'c', 'd'],
      ['a', 'b', 'f', 'e'],
      ['b', 'c', 'g', 'f'],
      ['c', 'd', 'h', 'g'],
      ['d', 'a', 'e', 'h'],
    ];
    const context = kindName(state.view?.context || '');

    // Faces.
    faces.forEach((face, index) => {
      ctx.beginPath();
      face.forEach((name, pointIndex) => {
        const [x, y] = corners[name];
        if (pointIndex === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.closePath();
      const highlight = context === 'polygon' || context === 'element';
      ctx.fillStyle = highlight
        ? `rgba(242,163,60,${0.06 + index * 0.015})`
        : `rgba(103,184,255,${0.03 + index * 0.008})`;
      ctx.fill();
      ctx.strokeStyle = highlight ? 'rgba(242,163,60,0.55)' : 'rgba(150,175,210,0.5)';
      ctx.lineWidth = context === 'edge' || context === 'border' ? 2.1 : 1;
      ctx.stroke();
    });

    // Vertices.
    const showVertices = context === 'vertex';
    for (const [name, [x, y]] of Object.entries(corners)) {
      ctx.beginPath();
      ctx.arc(x, y, showVertices ? 4.2 : 2.1, 0, Math.PI * 2);
      ctx.fillStyle = showVertices ? '#f2a33c' : 'rgba(220,232,255,0.75)';
      ctx.fill();
      if (showVertices) {
        ctx.strokeStyle = 'rgba(242,163,60,0.35)';
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.arc(x, y, 8, 0, Math.PI * 2); ctx.stroke();
      }
      void name;
    }

    // Element silhouette.
    if (context === 'element') {
      ctx.strokeStyle = 'rgba(242,163,60,0.85)';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ['a', 'b', 'c', 'd'].forEach((name, index) => {
        const [x, y] = corners[name];
        if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.closePath(); ctx.stroke();
    }

    // Origin / axes.
    ctx.strokeStyle = 'rgba(255,255,255,0.14)';
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(28, height - 34); ctx.lineTo(68, height - 34); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(28, height - 34); ctx.lineTo(28, height - 72); ctx.stroke();
    ctx.fillStyle = '#ff7a7a'; ctx.font = '10px monospace'; ctx.fillText('X', 72, height - 31);
    ctx.fillStyle = '#5fd39a'; ctx.fillText('Y', 24, height - 78);

    // Watermark.
    ctx.fillStyle = 'rgba(255,255,255,0.10)';
    ctx.font = '600 10px monospace';
    ctx.fillText('SIMULATED GEOMETRY · NO SCENE DATA', 14, height - 12);
  }

  /* ------------------------------------------------------------------ */
  /* Boot                                                                */
  /* ------------------------------------------------------------------ */

  function bindMisc() {
    const viewport = $('#viewport');
    viewport?.addEventListener('focus', () => {
      viewport.classList.add('has-focus');
    });
    viewport?.addEventListener('blur', () => {
      viewport.classList.remove('has-focus');
    });
    $('#scenario-select')?.addEventListener('change', (event) => chooseScenario(event.target.value));
    window.addEventListener('resize', drawViewport);
    if (typeof ResizeObserver !== 'undefined') {
      const observer = new ResizeObserver(() => drawViewport());
      observer.observe(viewport);
    }
  }

  function bindEvents() {
    bindPointer();
    bindKeyboard();
    bindCancellation();
    bindMenu();
    bindGuided();
    bindProfile();
    bindMisc();
  }

  async function boot() {
    bindEvents();
    try {
      await refreshState();
    } catch (error) {
      setConnection('bad', 'disconnected');
      reportError(error);
    }
  }

  // Stable names for browser tooling and unit-level inspection.  These are
  // render plumbing only; the Python controller remains the state machine.
  window.SandboxClient = {
    request: api,
    enqueue,
    refresh: refreshState,
    state,
  };
  window.renderMenu = renderMenu;
  window.renderState = renderState;
  window.ProfileEditor = {
    render: renderProfileList,
    apply: applyProfile,
    reset: resetProfile,
    exportJson: exportProfile,
    importJson: importProfile,
  };

  boot();
})();
