'use strict';
const $ = id => document.getElementById(id);
let state = {}, modifying = false, busy = false, polling = false, toastTimer;
function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}
function toast(text, error = false) {
  clearTimeout(toastTimer);
  $('status').textContent = text;
  $('status').className = 'status' + (error ? ' error' : '');
  toastTimer = setTimeout(() => $('status').classList.add('hidden'), error ? 15000 : 5000);
}
async function api(path, data) {
  const response = await fetch(path, {method: data === undefined ? 'GET' : 'POST',
    headers: {'Content-Type': 'application/json', 'X-CSRF-Token': state.csrf || ''},
    ...(data === undefined ? {} : {body: JSON.stringify(data)})});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Request failed. Try again.');
  return result;
}
async function action(fn) {
  if (busy) return;
  busy = true;
  document.querySelectorAll('button').forEach(b => b.disabled = true);
  try {await fn();} catch (e) {toast(e.message, true);}
  finally {busy = false; document.querySelectorAll('button').forEach(b => b.disabled = false);}
}
function formatTime(value) {
  return new Intl.DateTimeFormat(undefined, {weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: state.profile.timezone}).format(new Date(value));
}
function render() {
  const auth = state.authenticated;
  $('welcome').classList.toggle('hidden', auth);
  $('workspace').classList.toggle('hidden', !auth);
  $('logout').classList.toggle('hidden', !auth);
  if (!auth) {
    if (!state.google_configured) $('credentials').textContent = 'Google connection is awaiting OAuth setup. The local demo is ready to explore.';
    return;
  }
  $('mode').textContent = state.demo ? 'Local demo' : 'Google Calendar connected';
  $('demo-banner').classList.toggle('hidden', !state.demo);
  $('calendar-link').classList.toggle('hidden', state.demo);
  $('interpreter').textContent = state.interpreter + ' · ' + state.profile.timezone;
  $('profile').textContent = `Work ${state.profile.work_start}:00–${state.profile.work_end}:00\nAwake ${state.profile.wake}:00–${state.profile.sleep}:00\nProtected reserve: ${state.profile.reserve_minutes} minutes\n` + (state.profile.context || []).map(c => c.answer).join('\n');
  const messages = $('messages'); messages.replaceChildren();
  for (const m of state.messages || []) {
    const el = node('div', undefined, 'bubble ' + m.role);
    el.dir = 'auto';
    el.append(node('span', m.role === 'user' ? 'YOU' : 'TIME & PLACE', 'bubble-label'), node('span', m.text));
    messages.append(el);
  }
  if (state.question && !(state.messages || []).length) {
    messages.append(node('div', state.question, 'bubble assistant'));
  }
  requestAnimationFrame(() => {messages.scrollTop = messages.scrollHeight;});
  $('input').placeholder = state.question ? 'Tell me a little about your rhythm…' : modifying ? 'For example: he works until 16:00 / הוא בעבודה עד 16:00' : 'Tomorrow I want to call my brother… / מחר אני צריך להתקשר לאחי';
  $('input-hint').textContent = modifying ? 'Revising the current proposal. Your correction will be remembered.' : 'One intention is enough to begin.';
  renderProposal();
}
function renderProposal() {
  const box = $('proposal'), p = state.proposal;
  box.replaceChildren(); box.classList.toggle('hidden', !p);
  if (!p) return;
  const a = p.achievement;
  box.append(node('div', p.reschedule ? 'A NEW MOMENT · SAME ACHIEVEMENT' : 'A LITTLE TIME, PROTECTED', 'eyebrow'));
  const title = node('h3', a.title + (a.note ? ' ⓘ' : '')); title.dir = 'auto'; box.append(title);
  const time = node('div', undefined, 'proposal-time');
  time.append(node('span', '◷'), node('span', formatTime(p.start) + ` · ${a.duration} min`)); box.append(time);
  if (a.location) box.append(node('div', '⌖ ' + a.location, 'small'));
  if (a.note) {const note = node('div', a.note, 'proposal-note'); note.dir = 'auto'; box.append(note);}
  box.append(node('p', p.reason, 'proposal-reason'));
  if (p.reschedule) box.append(node('p', 'Your existing event stays in place until you approve this move.', 'small'));
  const buttons = node('div', undefined, 'proposal-buttons');
  const approve = node('button', state.demo ? 'Approve in demo ✓' : p.reschedule ? 'Approve move ✓' : 'Approve & add to Google ✓', 'button primary');
  approve.onclick = () => action(async () => {await api('/api/approve', {id: p.id}); modifying = false; await refresh(); await sync(); toast(state.demo ? 'Scheduled in the local demo. Google Calendar was not changed.' : 'Time protected in Google Calendar.');});
  const modify = node('button', 'Find a better fit', 'button secondary');
  modify.onclick = () => {modifying = true; render(); $('input').focus();};
  const dismiss = node('button', 'Not now', 'quiet');
  dismiss.onclick = () => action(async () => {await api('/api/dismiss', {id: p.id}); modifying = false; await refresh();});
  buttons.append(approve, modify, dismiss); box.append(buttons);
}
async function refresh() {state = await api('/api/state'); render();}
async function sync() {
  if (!state.authenticated || polling) return;
  polling = true;
  try {
    const result = await api('/api/sync');
    $('sync-label').textContent = state.demo ? 'Example calendar synced' : 'Google Calendar synced';
    $('sync-info').textContent = `${result.today_count} commitment${result.today_count === 1 ? '' : 's'} today · ${new Intl.DateTimeFormat(undefined, {hour:'2-digit',minute:'2-digit',timeZone:state.profile.timezone}).format(new Date(result.synced_at))}`;
    await refresh();
  } catch(e) {$('sync-label').textContent = 'Calendar needs attention'; $('sync-info').textContent = e.message; toast(e.message, true);}
  finally {polling = false;}
}
$('demo').onclick = () => action(async () => {await api('/api/demo', {}); await refresh(); await sync();});
$('logout').onclick = () => action(async () => {await api('/api/logout', {}); modifying = false; $('review').classList.add('hidden'); await refresh();});
$('sync').onclick = () => action(sync);
$('chat-form').onsubmit = event => {
  event.preventDefault();
  const text = $('input').value.trim(); if (!text) return;
  action(async () => {
    await api('/api/chat', {text, ...(modifying && state.proposal ? {proposal_id: state.proposal.id} : {})});
    $('input').value = ''; modifying = false; $('review').classList.add('hidden'); await refresh();
  });
};
$('input').addEventListener('keydown', e => {if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {e.preventDefault(); $('chat-form').requestSubmit();}});
$('daily').onclick = () => action(async () => {
  await sync();
  const review = $('review'); review.replaceChildren(); review.classList.remove('hidden');
  review.append(node('h3', 'A gentle close to the day'), node('p', 'Would you like to mark anything complete, or find another moment? There’s no overdue list here.'));
  const today = new Intl.DateTimeFormat('en-CA', {timeZone: state.profile.timezone,year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
  const scheduled = state.achievements.filter(a => a.status === 'scheduled' && a.data.scheduled_start);
  const items = scheduled.filter(a => {
    const date = a.data.scheduled_start.date || new Intl.DateTimeFormat('en-CA', {timeZone: state.profile.timezone,year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(a.data.scheduled_start.dateTime));
    return date <= today;
  });
  if (!items.length) review.append(node('p', 'Nothing scheduled through this app needs a check-in today. Enjoy your evening.'));
  for (const a of items) {
    const row = node('div', undefined, 'review-row'); row.append(node('div', a.data.title));
    const done = node('button', 'Completed ✓', 'quiet');
    done.onclick = () => action(async () => {await api('/api/complete', {id:a.id}); row.remove(); await refresh(); toast('Achievement acknowledged.');});
    const move = node('button', 'Find another moment', 'quiet');
    move.onclick = () => action(async () => {await api('/api/reschedule', {id:a.id}); review.classList.add('hidden'); modifying = false; await refresh();});
    row.append(done, move); review.append(row);
  }
  const close = node('button', 'That’s enough for today', 'quiet'); close.onclick = () => review.classList.add('hidden'); review.append(close);
});
const authStatus = new URLSearchParams(location.search).get('auth');
if (authStatus) {toast(authStatus === 'not_configured' ? 'Google OAuth is not configured yet. You can explore the local demo.' : 'Google sign-in was cancelled.', true); history.replaceState({}, '', '/');}
refresh().then(sync).catch(e => toast(e.message, true));
setInterval(() => {if (!document.hidden && !busy && !modifying) sync();}, 60000);
window.addEventListener('focus', () => {if (!busy && !modifying) sync();});
