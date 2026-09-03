/* Eseguire con: node tests/test_auction_ui.js (nessuna dipendenza esterna). */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const helpers = fs.readFileSync(path.join(root, 'frontend/auction-ui.js'), 'utf8');

function functionSource(file, name) {
  const html = fs.readFileSync(path.join(root, file), 'utf8');
  const start = html.search(new RegExp('(?:async )?function ' + name + '\\('));
  assert(start >= 0, name);
  for (let end = html.indexOf('}', start); end >= 0; end = html.indexOf('}', end + 1)) {
    const source = html.slice(start, end + 1);
    try { new vm.Script('(' + source + ')'); return source; } catch (_) {}
  }
  throw new Error('Cannot extract ' + name);
}

class Element {
  constructor() { this.children = []; this.text = ''; this.className = ''; this.parentNode = null; }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return this.text + this.children.map(c => c.textContent).join(''); }
  get childNodes() { return this.children; }
  get firstChild() { return this.children[0] || null; }
  get nextSibling() { return this.parentNode?.children[this.parentNode.children.indexOf(this) + 1] || null; }
  append(...children) { children.forEach(child => this.appendChild(child)); }
  appendChild(child) { this.insertBefore(child, null); return child; }
  insertBefore(child, before) {
    if (child.parentNode) child.parentNode.removeChild(child);
    const index = before ? this.children.indexOf(before) : this.children.length;
    assert(index >= 0);
    this.children.splice(index, 0, child); child.parentNode = this;
  }
  removeChild(child) { this.children.splice(this.children.indexOf(child), 1); child.parentNode = null; }
  replaceChildren() { this.children.forEach(child => child.parentNode = null); this.children = []; this.text = ''; }
  querySelector(selector) {
    for (const child of this.children) {
      if (child.className.split(' ').includes(selector.slice(1))) return child;
      const found = child.querySelector(selector); if (found) return found;
    }
    return null;
  }
}

function baseScope() {
  const nodes = new Map();
  const count = new Element(), label = new Element();
  const card = {querySelector: selector => selector === '.auto-count' ? count : label};
  const scope = {
    state: null, currentView: 'auction', myTeam: 'Alpha',
    serverClockOffsetMs: 0, lastStateVersion: 0, lastRosterKey: null, lastAllRostersKey: null,
    timerInt: null, autoInt: null, toccaUiTimer: null, renders: 0, resultNode: null,
    document: {
      body: {classList: {toggle() {}}}, createElement: () => new Element(),
      querySelector: selector => selector === '.auto-random-card' ? card : count,
    },
    $: id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); },
    setInterval: () => 1, clearInterval() {}, clearTimeout() {},
    samePendingTocca: () => false, toccaWinnerHoldActive: () => false,
    rosterKey: () => '', releaseRenderKey: () => '', currentBidDraftKey: () => '',
    captureBidDraft() {}, clearBidDraft() {}, updateHeader() {}, showMandatoryPinChange() {},
    renderAuction() { scope.renders++; scope.resultNode = {detailsOpen: false}; },
    requestExtraTime() {}, playerHero: () => new Element(), progressLine: () => new Element(),
    timerBlock: () => new Element(), bidCard: () => new Element(),
  };
  const context = vm.createContext(scope);
  vm.runInContext(helpers, context);
  return {context, count, label};
}

function state() {
  return {
    mode: 'idle', auction_started: true, simulation: false, catalog_revision: '1',
    last_result: {type: 'assigned', pid: 1, team: 'Alpha', price: 7, reveal: {Alpha: 20, Beta: 6}},
    random_pool: {eligible_count: 25, min_quotation: 4}, auto_random: true,
    auto_random_seconds: 10, active_market_count: 2,
    me: {ready: true, is_manager: false, market_finished: false},
    teams: [{name: 'Alpha', ready: true, market_finished: false, presence: {last_seen: 1}}],
    state_version: 1, server_now_ms: 1000,
  };
}

for (const file of ['frontend/auction.html', 'frontend/spectator.html']) {
  const {context, count} = baseScope();
  const names = ['allRostersKey', 'handleState'];
  if (file.endsWith('/auction.html')) names.push('startAutoCountdown', 'syncRandomIdleRealtime');
  vm.runInContext(names.map(name => functionSource(file, name)).join('\n'), context);
  context.handleState(state());
  assert.equal(context.renders, 1);
  const original = context.resultNode;
  original.detailsOpen = true;
  const tick = state();
  tick.state_version = 2; tick.server_now_ms = 9000; tick.auto_random_seconds = 4;
  tick.teams[0].presence.last_seen = 9;
  context.handleState(tick);
  assert.equal(context.renders, 1, 'Identical summary must not be rebuilt');
  assert.equal(context.resultNode, original);
  assert.equal(context.resultNode.detailsOpen, true);
  assert.equal(context.lastStateVersion, 2, 'Realtime bookkeeping must continue');
  if (file.endsWith('/auction.html')) assert.equal(count.textContent, '4');

  // Un risultato corretto, un nuovo sorteggio e la pausa dell'automatismo
  // devono continuare a produrre gli aggiornamenti visibili necessari.
  const corrected = structuredClone(tick); corrected.last_result.price = 8;
  context.handleState(corrected); assert.equal(context.renders, 2);
  const stopped = structuredClone(corrected); stopped.auto_random_seconds = null;
  stopped.auto_random = false; context.handleState(stopped); assert.equal(context.renders, 3);
  const bidding = {...stopped, mode: 'bidding', current_player: {pid: 2}};
  context.handleState(bidding); assert.equal(context.renders, 4);
  context.handleState({...bidding, mode: 'tocca'}); assert.equal(context.renders, 5);
  console.log(file + ': stable summary, preserved details, countdown and real changes OK');
}

for (const file of ['frontend/auction.html', 'frontend/spectator.html']) {
  const {context} = baseScope();
  const participant = file.endsWith('/auction.html');
  const names = ['el', 'clear', participant ? 'renderBidding' : 'renderAuction'];
  if (participant) names.push('extraTimeBlock');
  else names.push('renderAuctionContent');
  vm.runInContext(names.map(name => functionSource(file, name)).join('\n'), context);
  for (const kind of ['team', 'manager', 'system']) {
    const box = context.$('view-auction'); box.replaceChildren();
    context.state = {...state(), mode: 'bidding', current_player: {pid: 1}, paused: true,
      paused_by: {kind, team: kind === 'system' ? null : 'Alpha'},
      eligible_teams: [], submitted: [], teams: [], extra_time: {requested_for_player: kind === 'team'}};
    if (participant) context.renderBidding(box); else context.renderAuction();
    assert(box.textContent.includes(kind === 'system' ? 'automaticamente dopo il riavvio' : 'Tempo bloccato da Alpha'));
    if (kind === 'manager') assert(box.textContent.includes('(Gestore)'));
  }
  context.state.paused = false;
  context.state.extra_time.requested_for_player = true;
  const box = context.$('view-auction'); box.replaceChildren();
  if (participant) context.renderBidding(box); else context.renderAuction();
  assert(!box.textContent.includes('Tempo bloccato'), 'A new tiebreak must not appear paused');
  console.log(file + ': pause attribution visible to all, including non-bidders; resumed timer OK');
}

{
  const {context} = baseScope();
  const box = new Element();
  const render = countdown => container => {
    const notice = new Element(); notice.className = 'notice';
    const result = new Element(); result.className = 'card result';
    const controls = new Element(); controls.textContent = countdown ? 'Countdown 10' : 'Manuale';
    container.append(notice, result, controls);
  };
  context.AuctionUI.renderAuctionView(box, state(), render(false));
  const result = box.querySelector('.result'); result.openDetails = true;
  const remove = box.removeChild.bind(box);
  box.removeChild = node => { assert.notEqual(node, result, 'The result must never be detached'); remove(node); };
  context.AuctionUI.renderAuctionView(box, {...state(), auto_random_seconds: 10}, render(true));
  assert.equal(box.querySelector('.result'), result);
  assert.equal(result.openDetails, true);
  assert(box.textContent.includes('Countdown 10'));
  context.AuctionUI.renderAuctionView(box, {...state(), auto_random_seconds: null}, render(false));
  assert.equal(box.querySelector('.result'), result);
  assert(box.textContent.includes('Manuale'));
  const changed = state(); changed.last_result.price++;
  context.AuctionUI.renderAuctionView(box, changed, render(false));
  assert.notEqual(box.querySelector('.result'), result, 'A changed result must be rendered');
  console.log('Countdown start/stop preserves the attached result node and open details');
}

for (const file of ['frontend/auction.html', 'frontend/spectator.html']) {
  const html = fs.readFileSync(path.join(root, file), 'utf8');
  for (const match of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)) new vm.Script(match[1]);
}
console.log('Inline JavaScript syntax OK');
