/* Testi pubblici della pausa e confronto del riepilogo condivisi dalle due viste. */
(function (root) {
  'use strict';

  function pauseMessage(state) {
    if (!state?.paused) return '';
    const actor = state.paused_by;
    let label = '⏸️ Tempo bloccato';
    if (actor?.kind === 'system') {
      label += ' automaticamente dopo il riavvio';
    } else if (actor?.team) {
      label += ` da ${actor.team}`;
      if (actor.kind === 'manager') label += ' (Gestore)';
    } else if (actor?.kind === 'manager') {
      label += ' dal Gestore';
    }
    return label + ' · la busta si apre appena tutti hanno risposto';
  }

  function idleSummaryKey(state) {
    if (state?.mode !== 'idle' || !state.last_result ||
        (!state.auction_started && !state.simulation)) return null;
    // Orologio, versione e presenza cambiano spesso senza modificare il
    // riepilogo. I secondi RANDOM vengono sincronizzati nel nodo esistente.
    return JSON.stringify({
      result: state.last_result,
      simulation: state.simulation,
      started: state.auction_started,
      catalog: state.catalog_revision,
      pool: state.random_pool,
      automatic: state.auto_random,
      countdown: state.auto_random_seconds != null,
      active: state.active_market_count,
      viewer: {
        manager: state.me?.is_manager,
        ready: state.me?.ready,
        finished: state.me?.market_finished,
      },
      teams: (state.teams || []).map(team => ({
        name: team.name, username: team.username,
        ready: team.ready, finished: team.market_finished,
      })),
    });
  }

  function sameIdleSummary(previous, next) {
    const key = idleSummaryKey(previous);
    return key !== null && key === idleSummaryKey(next);
  }

  const renderedResults = new WeakMap();

  function renderAuctionView(container, state, render) {
    const key = state?.mode === 'idle' && state.last_result
      ? JSON.stringify(state.last_result) : null;
    const previous = renderedResults.get(container);
    if (key !== null && previous?.key === key && previous.node?.parentNode === container) {
      // Quando parte/si ferma il countdown cambiano i controlli, ma il
      // risultato resta identico: conservarne il nodo, immagini e <details>.
      const nextView = document.createElement('div');
      render(nextView);
      const nextResult = nextView.querySelector('.result');
      if (nextResult) {
        for (const node of Array.from(container.childNodes)) {
          if (node !== previous.node) container.removeChild(node);
        }
        let beforeResult = true;
        for (const node of Array.from(nextView.childNodes)) {
          if (node === nextResult) beforeResult = false;
          else if (beforeResult) container.insertBefore(node, previous.node);
          else container.appendChild(node);
        }
        return;
      }
    }
    container.replaceChildren();
    render(container);
    renderedResults.set(container, {key, node: key === null ? null : container.querySelector('.result')});
  }

  root.AuctionUI = {pauseMessage, sameIdleSummary, renderAuctionView};
})(globalThis);
