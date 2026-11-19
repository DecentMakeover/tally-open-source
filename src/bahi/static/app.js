'use strict';
/* Bahi web UI: a dependency-free single page app over the JSON API. */

// ---------------------------------------------------------------- helpers
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const INR = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const QTY = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 3 });
const num = (v) => Number(String(v ?? '').replace(/,/g, '')) || 0;
const fm = (v) => INR.format(num(v));
const fb = (v) => (num(v) ? INR.format(num(v)) : '');
const fq = (v) => (num(v) ? QTY.format(num(v)) : '');
const drcr = (v) => (num(v) ? `${INR.format(Math.abs(num(v)))} ${num(v) < 0 ? 'Cr' : 'Dr'}` : '');
const MONTHS = 'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split(' ');
const fdate = (s) => (s ? `${+s.slice(8, 10)}-${MONTHS[+s.slice(5, 7) - 1]}-${s.slice(0, 4)}` : '');
const today = () => {
  const d = new Date();
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 10);
};
const fyStart = (s) => `${+s.slice(5, 7) >= 4 ? +s.slice(0, 4) : +s.slice(0, 4) - 1}-04-01`;
const store = {
  get(k, fallback = null) { try { const v = localStorage.getItem(k); return v === null ? fallback : JSON.parse(v); } catch { return fallback; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};

async function api(path, opts = {}) {
  const init = { method: opts.method || 'GET', headers: { 'Content-Type': 'application/json' } };
  if (opts.body !== undefined) init.body = JSON.stringify(opts.body);
  const res = await fetch(`/api${path}`, init);
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    const msg = Array.isArray(d) ? d.map((x) => `${(x.loc || []).slice(1).join('.')}: ${x.msg}`).join('; ') : d;
    throw new Error(msg || `Request failed (${res.status})`);
  }
  return data;
}

function toast(msg, kind = 'ok', extra = '') {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.innerHTML = `<span>${esc(msg)}</span>${extra}`;
  $('#toasts').append(el);
  setTimeout(() => el.remove(), kind === 'err' ? 7000 : 4000);
}

function fail(err) {
  console.error(err);
  toast(err.message || String(err), 'err');
}

function download(name, text, type = 'text/csv') {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ------------------------------------------------------------------ state
const S = {
  meta: null, cid: null, company: null,
  ledgers: [], groups: [], items: [], units: [],
  from: '', to: '', view: '', vtype: 'Sales', lastDate: '', tree: {},
};
const C = (path) => `/c/${S.cid}${path}`;
const period = () => `frm=${S.from}&to=${S.to}`;
const go = (view, arg) => {
  const hash = `#/c/${S.cid}/${view}${arg !== undefined ? `/${arg}` : ''}`;
  if (location.hash === hash) route(); else location.hash = hash;
};
const main = () => $('#main');
const lower = (s) => String(s || '').trim().toLowerCase();
const ledgerByName = (name) => S.ledgers.find((l) => lower(l.name) === lower(name));
const itemByName = (name) => S.items.find((i) => lower(i.name) === lower(name));
const groupById = (id) => S.groups.find((g) => g.id === id);
const signed = (amount, side) => (side === 'cr' ? -num(amount) : num(amount));
const isInv = (t) => S.meta.invoice_types.includes(t);
const salesSide = (t) => t === 'Sales' || t === 'Credit Note';

function underGroup(gid, name) {
  for (let g = groupById(gid); g; g = groupById(g.parent_id)) if (g.name === name) return true;
  return false;
}

const stateOptions = (sel, blank = false) =>
  (blank ? '<option value="">-</option>' : '') +
  S.meta.states.map((s) => `<option value="${s.code}" ${s.code === sel ? 'selected' : ''}>${s.code} ${esc(s.name)}</option>`).join('');

async function loadMasters() {
  [S.ledgers, S.groups, S.items, S.units] = await Promise.all(
    ['/ledgers', '/groups', '/items', '/units'].map((p) => api(C(p))),
  );
}

// ------------------------------------------------------------- navigation
const NAV = [
  { view: 'home', label: 'Gateway', key: 'G' },
  { section: 'Transactions' },
  { view: 'voucher', label: 'Voucher entry', key: 'V' },
  { view: 'daybook', label: 'Day Book', key: 'D' },
  { section: 'Reports' },
  { view: 'bs', label: 'Balance Sheet', key: 'B' },
  { view: 'pl', label: 'Profit & Loss', key: 'P' },
  { view: 'trial', label: 'Trial Balance', key: 'T' },
  { view: 'ledger', label: 'Ledger statement', key: 'E' },
  { view: 'stock', label: 'Stock Summary', key: 'S' },
  { view: 'gst', label: 'GST Summary', key: 'X' },
  { section: 'Masters' },
  { view: 'ledgers', label: 'Ledgers', key: 'L' },
  { view: 'groups', label: 'Groups', key: 'A' },
  { view: 'items', label: 'Stock Items', key: 'I' },
  { view: 'company', label: 'Company', key: 'C' },
];
const FKEYS = { F4: 'Contra', F5: 'Payment', F6: 'Receipt', F7: 'Journal', F8: 'Sales', F9: 'Purchase', 'Ctrl+F8': 'Credit Note', 'Ctrl+F9': 'Debit Note' };
const VKEY = Object.fromEntries(Object.entries(FKEYS).map(([k, v]) => [v, k]));

function renderNav() {
  $('#nav').innerHTML = NAV.map((n) =>
    n.section
      ? `<div class="nav-sec">${n.section}</div>`
      : `<a href="#/c/${S.cid}/${n.view}" data-view="${n.view}" class="${S.view === n.view ? 'on' : ''}"><span>${n.label}</span><kbd>${n.key}</kbd></a>`,
  ).join('');
}

function renderHelp() {
  const rows = [
    ...NAV.filter((n) => n.key).map((n) => [n.key, n.label]),
    ...Object.entries(FKEYS).map(([k, v]) => [k, `New ${v}`]),
    ['Enter', 'Next field (in a voucher)'],
    ['Ctrl+Enter', 'Save voucher'],
    ['N', 'New master (on master screens)'],
    ['/', 'Search the list'],
    ['Esc', 'Leave field, then back to Gateway'],
  ];
  $('#help-body').innerHTML = `<table class="tbl keys">${rows.map(([k, v]) => `<tr><td><kbd>${esc(k)}</kbd></td><td>${esc(v)}</td></tr>`).join('')}</table>`;
}

async function openCompany(cid) {
  S.cid = cid;
  S.company = await api(C('/company'));
  const saved = store.get(`bahi:period:${cid}`);
  const fy = fyStart(today());
  S.from = saved?.from || (fy < S.company.books_from ? S.company.books_from : fy);
  S.to = saved?.to || today();
  S.tree = {};
  await loadMasters();
  document.body.classList.remove('no-company');
  $('#co-name').innerHTML = `<strong>${esc(S.company.name)}</strong><span>${esc(S.company.gstin || 'GST unregistered')}</span>`;
  $('#p-from').value = S.from;
  $('#p-to').value = S.to;
}

function setPeriod() {
  const f = $('#p-from').value, t = $('#p-to').value;
  if (!f || !t) return;
  S.from = f; S.to = t;
  store.set(`bahi:period:${S.cid}`, { from: f, to: t });
  route();
}

const VIEWS = {};

async function route() {
  const parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  try {
    if (!S.meta) S.meta = await api('/meta');
    if (parts[0] !== 'c' || !parts[1]) {
      S.cid = null;
      document.body.classList.add('no-company');
      return await viewCompanies();
    }
    if (S.cid !== parts[1] || !S.company) await openCompany(parts[1]);
    S.view = VIEWS[parts[2]] ? parts[2] : 'home';
    renderNav();
    window.scrollTo(0, 0);
    await VIEWS[S.view](parts.slice(3).map(decodeURIComponent).join('/') || undefined);
  } catch (err) {
    fail(err);
    if (String(err.message).startsWith('No company')) location.hash = '#/';
  }
}

// --------------------------------------------------------------- companies
async function viewCompanies() {
  const list = await api('/companies');
  $('#co-name').innerHTML = '';
  main().innerHTML = `
  <section class="welcome">
    <h1>Books</h1>
    <p class="muted lead">Double-entry accounting with GST invoicing. Everything stays in files on this computer.</p>
    <div class="co-list">${list.map((c) => `
      <a class="co-card" href="#/c/${esc(c.id)}">
        <strong>${esc(c.name)}</strong>
        <span>${esc(c.state_name)}${c.gstin ? ` &middot; ${esc(c.gstin)}` : ''}</span>
        <span class="muted">Books from ${fdate(c.books_from)}</span>
      </a>`).join('') || '<p class="muted">No companies yet. Create one, or open the demo company to look around.</p>'}
    </div>
    <div class="split">
      <form id="new-co" class="card form">
        <h2>Create a company</h2>
        <label>Name<input name="name" required autofocus></label>
        <div class="row2">
          <label>State<select name="state_code" required><option value="">Choose...</option>${stateOptions('')}</select></label>
          <label>Books begin from<input type="date" name="books_from" value="${fyStart(today())}" required></label>
        </div>
        <label>GSTIN <span class="muted">(leave blank if not registered)</span><input name="gstin" maxlength="15" class="caps"></label>
        <label>Address<textarea name="address" rows="2"></textarea></label>
        <button class="primary">Create company</button>
      </form>
      <div class="card">
        <h2>Explore</h2>
        <p class="muted">A fictional trading company with stock, GST invoices, receipts and payments.</p>
        <button id="demo-btn">Create the demo company</button>
        <h2 class="mt">Restore a backup</h2>
        <p class="muted">Pick a <code>.json</code> backup downloaded from a company's settings page.</p>
        <input type="file" id="restore" accept=".json,application/json">
      </div>
    </div>
  </section>`;
  const form = $('#new-co');
  form.gstin.addEventListener('input', () => {
    const g = form.gstin.value.trim();
    if (/^\d{2}/.test(g) && !form.state_code.value) form.state_code.value = g.slice(0, 2);
  });
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      const { id } = await api('/companies', { method: 'POST', body: Object.fromEntries(new FormData(form)) });
      location.hash = `#/c/${id}`;
    } catch (err) { fail(err); }
  });
  $('#demo-btn').onclick = async () => {
    try { const { id } = await api('/companies/demo', { method: 'POST' }); location.hash = `#/c/${id}`; } catch (err) { fail(err); }
  };
  $('#restore').onchange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try {
      const { id } = await api('/companies/import', { method: 'POST', body: JSON.parse(await file.text()) });
      toast('Backup restored');
      location.hash = `#/c/${id}`;
    } catch (err) { fail(err); }
  };
}

// -------------------------------------------------------------------- home
VIEWS.home = async () => {
  const [pl, db] = await Promise.all([api(C(`/reports/profit-loss?${period()}`)), api(C(`/reports/day-book?${period()}`))]);
  const sum = (f) => S.ledgers.filter(f).reduce((a, l) => a + signed(l.closing_balance, l.closing_side), 0);
  const sales = pl.trading.credit.find((n) => n.name === 'Sales Accounts')?.amount || 0;
  const tile = (label, value, hint, href) => `
    <a class="tile" href="${href}"><span class="muted">${label}</span><strong>${value}</strong><small class="muted">${hint}</small></a>`;
  const recent = db.vouchers.slice(-8).reverse();
  main().innerHTML = `
  <div class="rhead"><div><h1>Gateway</h1><p class="muted">${fdate(S.from)} to ${fdate(S.to)}</p></div></div>
  <div class="tiles">
    ${tile('Cash and bank', drcr(sum((l) => l.is_cash_bank)) || '0.00', 'balance now', `#/c/${S.cid}/trial`)}
    ${tile('Receivable', drcr(sum((l) => l.is_debtor)) || '0.00', 'sundry debtors', `#/c/${S.cid}/trial`)}
    ${tile('Payable', drcr(sum((l) => l.is_creditor)) || '0.00', 'sundry creditors', `#/c/${S.cid}/trial`)}
    ${tile('Sales', fm(sales), 'in this period', `#/c/${S.cid}/pl`)}
    ${tile(num(pl.net_profit) < 0 ? 'Net loss' : 'Net profit', fm(Math.abs(num(pl.net_profit))), 'in this period', `#/c/${S.cid}/pl`)}
  </div>
  <div class="split">
    <section class="card">
      <h2>Enter a voucher</h2>
      <div class="quick">${S.meta.voucher_types.map((t) => `<a class="btn" href="#/c/${S.cid}/voucher/${encodeURIComponent(t)}">${esc(t)} <kbd>${VKEY[t]}</kbd></a>`).join('')}</div>
    </section>
    <section class="card">
      <h2>Recent vouchers</h2>
      ${recent.length ? `<table class="tbl click">${recent.map((v) => `
        <tr data-href="#/c/${S.cid}/voucher/edit-${v.id}"><td class="nowrap">${fdate(v.date)}</td><td>${esc(v.particulars)}</td>
        <td class="muted">${esc(v.vtype)} ${esc(v.number)}</td><td class="num">${fm(v.amount)}</td></tr>`).join('')}</table>`
        : '<p class="muted">No vouchers in this period yet.</p>'}
    </section>
  </div>`;
};

// ---------------------------------------------------------------- day book
VIEWS.daybook = async () => {
  const vt = store.get('bahi:daybook-type', '');
  const db = await api(C(`/reports/day-book?${period()}${vt ? `&vtype=${encodeURIComponent(vt)}` : ''}`));
  let total = 0;
  main().innerHTML = `
  ${reportHead('Day Book', `${fdate(db.from)} to ${fdate(db.to)}`, `
    <select id="db-type" aria-label="Voucher type"><option value="">All vouchers</option>
    ${S.meta.voucher_types.map((t) => `<option ${t === vt ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select>`)}
  <div class="card flush"><div class="tblwrap"><table class="tbl click">
    <thead><tr><th>Date</th><th>Particulars</th><th>Type</th><th>No.</th><th class="num">Amount</th><th class="noprint"></th></tr></thead>
    <tbody>${db.vouchers.map((v) => { total += num(v.amount); return `
      <tr data-href="#/c/${S.cid}/voucher/edit-${v.id}"><td class="nowrap">${fdate(v.date)}</td>
      <td>${esc(v.particulars)}${v.narration ? `<small class="muted block">${esc(v.narration)}</small>` : ''}</td>
      <td>${esc(v.vtype)}</td><td>${esc(v.number)}</td><td class="num">${fm(v.amount)}</td>
      <td class="noprint"><a href="#/c/${S.cid}/print/${v.id}" class="muted" title="Print">Print</a></td></tr>`; }).join('')
      || '<tr><td colspan="6" class="muted">No vouchers in this period.</td></tr>'}</tbody>
    <tfoot><tr><td colspan="4">${db.vouchers.length} vouchers</td><td class="num">${fm(total)}</td><td class="noprint"></td></tr></tfoot>
  </table></div></div>`;
  $('#db-type').onchange = (e) => { store.set('bahi:daybook-type', e.target.value); route(); };
};

// ------------------------------------------------------------ voucher entry
let V = null;
let previewTimer = null;

function defaultDate() {
  const d = S.lastDate || today();
  return d < S.company.books_from ? S.company.books_from : d;
}

const blankLine = () => ({ item: '', qty: '', rate: '', amount: '', gst_rate: '' });

function blankVoucher(vtype, date) {
  const v = {
    id: null, vtype, date: date || defaultDate(), number: '', reference: '', narration: '',
    entries: [], party: '', main: '', pos: '', posTouched: false, lines: [], round_off: true,
  };
  if (isInv(vtype)) {
    v.main = S.ledgers.find((l) => (salesSide(vtype) ? l.is_sales : l.is_purchase))?.name || '';
    v.lines = [blankLine()];
  } else {
    v.entries = [{ side: 'dr', ledger: '', amount: '' }, { side: 'cr', ledger: '', amount: '' }];
  }
  return v;
}

function fromServer(v) {
  const roundedOff = !(num(v.totals.round_off) === 0 && Math.round(num(v.amount) * 100) % 100 !== 0);
  return {
    id: v.id, vtype: v.vtype, date: v.date, number: v.number, reference: v.reference, narration: v.narration,
    entries: v.entries.map((e) => ({ side: e.side, ledger: e.ledger_name, amount: e.amount })),
    party: v.party_name || '', main: v.main_ledger_name || '', pos: v.place_of_supply, posTouched: true,
    lines: [...v.lines.map((l) => ({ item: l.item_name || l.description, qty: l.qty || '', rate: l.rate || '', amount: l.taxable, gst_rate: l.gst_rate })), blankLine()],
    round_off: roundedOff,
  };
}

VIEWS.voucher = async (arg) => {
  if (arg && arg.startsWith('edit-')) {
    V = fromServer(await api(C(`/vouchers/${arg.slice(5)}`)));
  } else {
    const t = arg && S.meta.voucher_types.includes(arg) ? arg : S.vtype;
    V = blankVoucher(t);
  }
  S.vtype = V.vtype;
  renderVoucher();
};

function switchType(t) {
  if (V && V.id) return;
  S.vtype = t;
  V = blankVoucher(t, V?.date);
  renderVoucher();
}

function balanceHint(name) {
  const l = ledgerByName(name);
  return l ? `Balance ${drcr(signed(l.closing_balance, l.closing_side)) || '0.00'}` : '';
}

function entryRow(e, i) {
  const l = ledgerByName(e.ledger);
  return `<tr data-row="${i}">
    <td><select data-k="e.${i}.side" aria-label="Dr or Cr"><option value="dr" ${e.side === 'dr' ? 'selected' : ''}>Dr</option><option value="cr" ${e.side === 'cr' ? 'selected' : ''}>Cr</option></select></td>
    <td><input list="dl-ledgers" data-k="e.${i}.ledger" value="${esc(e.ledger)}" placeholder="Ledger" class="${e.ledger && !l ? 'bad' : ''}" aria-label="Ledger">
      <small class="hint">${esc(balanceHint(e.ledger))}</small></td>
    <td class="num"><input data-k="e.${i}.amount" value="${esc(e.amount)}" inputmode="decimal" class="num" aria-label="Amount"></td>
    <td><button type="button" class="x" data-del="e.${i}" tabindex="-1" title="Remove line">&times;</button></td>
  </tr>`;
}

function lineHint(l) {
  const it = itemByName(l.item);
  if (it) return `${esc(it.unit)} &middot; HSN ${esc(it.hsn || '-')} &middot; GST ${esc(it.gst_rate)}%`;
  return l.item ? 'Service / non-stock line' : '';
}

function lineRow(l, i) {
  const it = itemByName(l.item);
  return `<tr data-row="${i}">
    <td><input list="dl-items" data-k="l.${i}.item" value="${esc(l.item)}" placeholder="Stock item or description" aria-label="Item">
      <small class="hint">${lineHint(l)}</small></td>
    <td class="num"><input data-k="l.${i}.qty" value="${esc(l.qty)}" inputmode="decimal" class="num" aria-label="Quantity"></td>
    <td class="num"><input data-k="l.${i}.rate" value="${esc(l.rate)}" inputmode="decimal" class="num" aria-label="Rate"></td>
    <td class="num"><input data-k="l.${i}.amount" value="${esc(l.amount)}" inputmode="decimal" class="num" aria-label="Amount"></td>
    <td class="num gstcol"><input data-k="l.${i}.gst_rate" value="${esc(l.gst_rate)}" placeholder="${it ? esc(it.gst_rate) : '0'}" inputmode="decimal" class="num sm" aria-label="GST rate"></td>
    <td><button type="button" class="x" data-del="l.${i}" tabindex="-1" title="Remove line">&times;</button></td>
  </tr>`;
}

function renderVoucher() {
  const inv = isInv(V.vtype);
  const reg = S.company.gst_registered;
  const sales = salesSide(V.vtype);
  const mains = S.ledgers.filter((l) => (sales ? l.is_sales : l.is_purchase));
  main().innerHTML = `
  <div class="vtabs" role="tablist">${S.meta.voucher_types.map((t) => `
    <button type="button" role="tab" class="vtab ${t === V.vtype ? 'on' : ''}" data-vt="${esc(t)}" ${V.id && t !== V.vtype ? 'disabled' : ''}>${esc(t)}<kbd>${VKEY[t]}</kbd></button>`).join('')}
  </div>
  <form id="vform" class="card voucher ${inv ? 'is-inv' : ''} ${reg ? '' : 'no-gst'}" autocomplete="off" novalidate>
    <div class="vmeta">
      <h1>${V.id ? 'Alter' : 'New'} ${esc(V.vtype)}</h1>
      <label>No.<input data-k="number" value="${esc(V.number)}" placeholder="auto" class="sm"></label>
      <label>Date<input type="date" data-k="date" value="${V.date}"></label>
      <label>Ref.<input data-k="reference" value="${esc(V.reference)}" placeholder="${inv && !sales ? 'Supplier bill no.' : ''}"></label>
    </div>
    ${inv ? `
    <div class="row3">
      <label>Party<input list="dl-parties" data-k="party" value="${esc(V.party)}" placeholder="Customer, supplier, cash or bank">
        <small class="hint" id="party-info"></small></label>
      <label>${sales ? 'Sales' : 'Purchase'} ledger<input list="dl-main" data-k="main" value="${esc(V.main)}"></label>
      <label class="gstcol">Place of supply<select data-k="pos">${stateOptions(V.pos || S.company.state_code)}</select></label>
    </div>
    <div class="tblwrap"><table class="tbl entry lines">
      <thead><tr><th>Item / description</th><th class="num">Qty</th><th class="num">Rate</th><th class="num">Amount</th><th class="num gstcol">GST %</th><th></th></tr></thead>
      <tbody id="rows">${V.lines.map(lineRow).join('')}</tbody>
    </table></div>
    <div class="invfoot">
      <label class="chk"><input type="checkbox" data-k="round_off" ${V.round_off ? 'checked' : ''}> Round off to the rupee</label>
      <div class="preview" id="preview"></div>
    </div>` : `
    <div class="tblwrap"><table class="tbl entry">
      <thead><tr><th class="w-side">Dr/Cr</th><th>Particulars</th><th class="num">Amount</th><th></th></tr></thead>
      <tbody id="rows">${V.entries.map(entryRow).join('')}</tbody>
      <tfoot><tr><td></td><td><button type="button" class="ghost sm" id="add-row">+ Line</button></td><td class="num" id="totals"></td><td></td></tr></tfoot>
    </table></div>`}
    <label class="narr">Narration<textarea data-k="narration" rows="2">${esc(V.narration)}</textarea></label>
    <div class="vfoot">
      <div class="err" id="verr"></div>
      ${V.id ? `<button type="button" class="ghost danger" id="vdel">Delete</button><a class="btn ghost" href="#/c/${S.cid}/print/${V.id}">Print</a>` : ''}
      <button class="primary" type="submit">${V.id ? 'Save changes' : 'Save'} <kbd>Ctrl+Enter</kbd></button>
    </div>
  </form>
  <datalist id="dl-ledgers">${S.ledgers.map((l) => `<option value="${esc(l.name)}">${esc(l.group_name)}</option>`).join('')}</datalist>
  <datalist id="dl-parties">${S.ledgers.filter((l) => l.is_party).map((l) => `<option value="${esc(l.name)}">${esc(l.group_name)}</option>`).join('')}</datalist>
  <datalist id="dl-main">${mains.map((l) => `<option value="${esc(l.name)}"></option>`).join('')}</datalist>
  <datalist id="dl-items">${S.items.map((i) => `<option value="${esc(i.name)}">${esc(i.unit)} &middot; GST ${esc(i.gst_rate)}%</option>`).join('')}</datalist>`;

  const form = $('#vform');
  form.addEventListener('input', onVoucherInput);
  form.addEventListener('change', onVoucherChange);
  form.addEventListener('keydown', onVoucherKey);
  form.addEventListener('focusin', prefillBalance);
  form.addEventListener('submit', (e) => { e.preventDefault(); saveVoucher(); });
  form.addEventListener('click', (e) => {
    const del = e.target.closest('[data-del]');
    if (!del) return;
    const [kind, i] = del.dataset.del.split('.');
    const arr = kind === 'e' ? V.entries : V.lines;
    if (arr.length > 1) arr.splice(+i, 1);
    redrawRows();
  });
  $$('.vtab').forEach((b) => { b.onclick = () => switchType(b.dataset.vt); });
  $('#add-row')?.addEventListener('click', () => { addEntryRow(); $(`[data-k="e.${V.entries.length - 1}.ledger"]`).focus(); });
  $('#vdel')?.addEventListener('click', deleteVoucher);
  if (inv) { updateParty(); schedulePreview(); } else updateTotals();
  const first = inv ? $('[data-k="party"]') : $('[data-k="e.0.ledger"]');
  first?.focus();
}

function redrawRows() {
  const inv = isInv(V.vtype);
  $('#rows').innerHTML = (inv ? V.lines.map(lineRow) : V.entries.map(entryRow)).join('');
  if (inv) schedulePreview(); else updateTotals();
}

function setField(k, value) {
  const [a, i, f] = k.split('.');
  if (f !== undefined) (a === 'e' ? V.entries : V.lines)[+i][f] = value;
  else V[k] = value;
}

function onVoucherInput(e) {
  const k = e.target.dataset.k;
  if (!k) return;
  const value = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
  setField(k, value);
  const [a, i, f] = k.split('.');
  if (a === 'l') {
    const l = V.lines[+i];
    if (f === 'qty' || f === 'rate') {
      if (num(l.qty) && l.rate !== '') {
        l.amount = (num(l.qty) * num(l.rate)).toFixed(2);
        $(`[data-k="l.${i}.amount"]`).value = l.amount;
      }
    } else if (f === 'amount' && itemByName(l.item) && l.rate !== '') {
      l.rate = '';
      $(`[data-k="l.${i}.rate"]`).value = '';
    }
  }
  if (isInv(V.vtype)) schedulePreview(); else updateTotals();
}

function onVoucherChange(e) {
  const k = e.target.dataset.k;
  if (!k) return;
  const [a, i, f] = k.split('.');
  if (k === 'party') updateParty(true);
  if (k === 'pos') V.posTouched = true;
  if (a === 'e') {
    const tr = e.target.closest('tr');
    if (f === 'ledger') {
      e.target.classList.toggle('bad', !!V.entries[+i].ledger && !ledgerByName(V.entries[+i].ledger));
      $('.hint', tr).textContent = balanceHint(V.entries[+i].ledger);
    }
    if (+i === V.entries.length - 1 && f !== 'side') {
      const last = V.entries[+i];
      const diff = entryDiff();
      if (last.ledger && num(last.amount) && Math.abs(diff) > 0.001) addEntryRow();
    }
  }
  if (a === 'l') {
    const tr = e.target.closest('tr');
    const l = V.lines[+i];
    if (f === 'item') {
      $('.hint', tr).innerHTML = lineHint(l);
      const it = itemByName(l.item);
      $(`[data-k="l.${i}.gst_rate"]`).placeholder = it ? it.gst_rate : '0';
      if (+i === V.lines.length - 1 && l.item) {
        V.lines.push(blankLine());
        $('#rows').insertAdjacentHTML('beforeend', lineRow(V.lines.at(-1), V.lines.length - 1));
      }
    }
  }
}

// Focusing an empty amount on the side that would balance the voucher fills in the difference.
function prefillBalance(e) {
  const k = e.target.dataset.k || '';
  const m = k.match(/^e\.(\d+)\.amount$/);
  if (!m || e.target.value) return;
  const row = V.entries[+m[1]];
  const diff = entryDiff();
  if (Math.abs(diff) < 0.001 || (diff > 0) !== (row.side === 'cr')) return;
  row.amount = Math.abs(diff).toFixed(2);
  e.target.value = row.amount;
  e.target.select();
  updateTotals();
}

function entryDiff() {
  return V.entries.reduce((a, e) => a + (e.side === 'dr' ? 1 : -1) * num(e.amount), 0);
}

function addEntryRow() {
  const diff = entryDiff();
  V.entries.push({ side: diff > 0 ? 'cr' : 'dr', ledger: '', amount: Math.abs(diff) > 0.001 ? Math.abs(diff).toFixed(2) : '' });
  $('#rows').insertAdjacentHTML('beforeend', entryRow(V.entries.at(-1), V.entries.length - 1));
  updateTotals();
}

function updateTotals() {
  const dr = V.entries.filter((e) => e.side === 'dr').reduce((a, e) => a + num(e.amount), 0);
  const cr = V.entries.filter((e) => e.side === 'cr').reduce((a, e) => a + num(e.amount), 0);
  const diff = dr - cr;
  $('#totals').innerHTML = `<span>Dr ${INR.format(dr)}</span><span>Cr ${INR.format(cr)}</span>${
    Math.abs(diff) > 0.001 ? `<span class="warn">Difference ${drcr(diff)}</span>` : (dr ? '<span class="good">Balanced</span>' : '')}`;
}

function updateParty(changed = false) {
  const p = ledgerByName(V.party);
  const info = $('#party-info');
  if (info) info.textContent = p ? [p.gstin || 'No GSTIN', p.state_code && S.meta.states.find((s) => s.code === p.state_code)?.name].filter(Boolean).join(' · ') : '';
  $('[data-k="party"]')?.classList.toggle('bad', !!V.party && !p);
  if (changed && p && !V.posTouched) {
    V.pos = p.state_code || S.company.state_code;
    const sel = $('[data-k="pos"]');
    if (sel) sel.value = V.pos;
  }
}

function payload() {
  const base = { vtype: V.vtype, date: V.date, number: V.number, reference: V.reference, narration: V.narration };
  if (!isInv(V.vtype)) {
    return {
      ...base,
      entries: V.entries.filter((e) => e.ledger || num(e.amount)).map((e) => ({
        ledger_id: ledgerByName(e.ledger)?.id ?? null, side: e.side, amount: e.amount || '0',
      })),
    };
  }
  return {
    ...base,
    party_ledger_id: ledgerByName(V.party)?.id ?? null,
    main_ledger_id: ledgerByName(V.main)?.id ?? null,
    place_of_supply: V.pos || '',
    round_off: !!V.round_off,
    lines: V.lines.filter((l) => l.item || num(l.amount)).map((l) => {
      const it = itemByName(l.item);
      if (it) return { stock_item_id: it.id, qty: l.qty || '0', rate: l.rate === '' ? null : l.rate, amount: l.rate === '' ? (l.amount || null) : null, gst_rate: l.gst_rate };
      return { description: l.item, amount: l.amount || '0', gst_rate: l.gst_rate };
    }),
  };
}

function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(runPreview, 250);
}

async function runPreview() {
  const box = $('#preview');
  if (!box) return;
  const body = payload();
  if (!body.party_ledger_id || !body.lines.length) {
    box.innerHTML = '<p class="muted">Pick a party and add lines to see tax and totals.</p>';
    return;
  }
  try {
    const p = await api(C('/vouchers/preview'), { method: 'POST', body });
    const t = p.totals;
    const row = (label, v, cls = '') => (num(v) || cls ? `<tr class="${cls}"><td>${label}</td><td class="num">${fm(v)}</td></tr>` : '');
    box.innerHTML = `
      <table class="tbl sums">
        ${row('Taxable value', t.taxable, 'keep')}${row('CGST', t.cgst)}${row('SGST', t.sgst)}${row('IGST', t.igst)}${row('Round off', t.round_off)}
        <tr class="grand"><td>Total</td><td class="num">${fm(p.amount)}</td></tr>
      </table>
      <p class="words">${esc(p.amount_in_words)}</p>
      <details><summary>Ledger posting</summary>
        <table class="tbl mini">${p.entries.map((e) => `<tr><td>${e.side === 'dr' ? 'Dr' : 'Cr'}</td><td>${esc(e.ledger_name)}</td><td class="num">${fm(e.amount)}</td></tr>`).join('')}</table>
      </details>`;
    p.lines.forEach((ln, i) => {
      const rate = $(`[data-k="l.${i}.rate"]`);
      if (rate && ln.rate) rate.placeholder = ln.rate;
    });
  } catch (err) {
    box.innerHTML = `<p class="muted">${esc(err.message)}</p>`;
  }
}

function onVoucherKey(e) {
  if (e.key !== 'Enter' || e.ctrlKey || e.metaKey) return;
  const t = e.target;
  if (t.tagName === 'TEXTAREA' || t.tagName === 'BUTTON' || t.type === 'checkbox') return;
  e.preventDefault();
  setTimeout(() => {
    const fields = $$('#vform input, #vform select, #vform textarea, #vform button[type=submit]').filter((el) => !el.disabled && el.offsetParent !== null);
    const i = fields.indexOf(t);
    fields[Math.min(i + 1, fields.length - 1)]?.focus();
  }, 0);
}

async function saveVoucher() {
  const err = $('#verr');
  err.textContent = '';
  try {
    const body = payload();
    const saved = V.id
      ? await api(C(`/vouchers/${V.id}`), { method: 'PUT', body })
      : await api(C('/vouchers'), { method: 'POST', body });
    if (!V.id) S.lastDate = V.date;
    toast(`${saved.vtype} ${saved.number} saved (${fm(saved.amount)})`, 'ok',
      `<a href="#/c/${S.cid}/print/${saved.id}">Print</a>`);
    loadMasters().catch(fail);
    if (V.id) { go('daybook'); return; }
    V = blankVoucher(V.vtype, V.date);
    renderVoucher();
  } catch (e) {
    err.textContent = e.message;
    toast(e.message, 'err');
  }
}

async function deleteVoucher() {
  if (!confirm(`Delete ${V.vtype} ${V.number}? This cannot be undone.`)) return;
  try {
    await api(C(`/vouchers/${V.id}`), { method: 'DELETE' });
    toast(`${V.vtype} ${V.number} deleted`);
    await loadMasters();
    go('daybook');
  } catch (e) { fail(e); }
}

// ----------------------------------------------------------------- reports
function reportHead(title, sub, actions = '') {
  return `<div class="rhead"><div><h1>${title}</h1><p class="muted">${sub}</p></div>
    <div class="ractions noprint">${actions}<button type="button" class="ghost" data-csv>CSV</button><button type="button" class="ghost" onclick="print()">Print</button></div></div>`;
}

function tableCSV() {
  const lines = [];
  $$('#main table.tbl').forEach((table) => {
    $$('tr', table).forEach((tr) => {
      if (tr.hidden) return;
      lines.push($$('th,td', tr).filter((c) => !c.classList.contains('noprint'))
        .map((c) => `"${c.innerText.replace(/\s+/g, ' ').trim().replace(/"/g, '""')}"`).join(','));
    });
    lines.push('');
  });
  download(`${S.view}-${S.to}.csv`, lines.join('\n'));
}

const nodeKey = (n) => `${n.kind[0]}${n.id ?? n.name}`;

function walk(nodes, fn, depth = 0, path = '') {
  for (const n of nodes) {
    const p = path ? `${path}/${nodeKey(n)}` : nodeKey(n);
    fn(n, depth, p);
    if (n.children?.length) walk(n.children, fn, depth + 1, p);
  }
}

function nameCell(n, depth, p) {
  const caret = n.children?.length ? `<button type="button" class="caret" data-tog="${p}" aria-label="Toggle"></button>` : '<span class="caret-sp"></span>';
  const label = n.kind === 'ledger' ? `<a href="#/c/${S.cid}/ledger/${n.id}">${esc(n.name)}</a>` : esc(n.name);
  return `<td class="name" style="--d:${depth}">${caret}${label}</td>`;
}

function isOpen(p, depth) { return S.tree[`${S.view}:${p}`] ?? depth === 0; }

function applyTree() {
  $$('#main tr[data-path]').forEach((tr) => {
    const parts = tr.dataset.path.split('/');
    let visible = true;
    for (let i = 1; i < parts.length; i++) {
      if (!isOpen(parts.slice(0, i).join('/'), i - 1)) { visible = false; break; }
    }
    tr.hidden = !visible;
    const tog = $('[data-tog]', tr);
    if (tog) tog.classList.toggle('open', isOpen(tr.dataset.path, +tr.dataset.depth));
  });
}

function bindReport() {
  main().onclick = (e) => {
    const tog = e.target.closest('[data-tog]');
    if (tog) {
      const tr = tog.closest('tr');
      const key = `${S.view}:${tog.dataset.tog}`;
      S.tree[key] = !isOpen(tog.dataset.tog, +tr.dataset.depth);
      applyTree();
      return;
    }
    if (e.target.closest('[data-csv]')) return tableCSV();
    const all = e.target.closest('[data-expand]');
    if (all) {
      const open = all.dataset.expand === '1';
      $$('#main tr[data-path]').forEach((tr) => { S.tree[`${S.view}:${tr.dataset.path}`] = open; });
      applyTree();
    }
  };
  applyTree();
}

const expandButtons = '<button type="button" class="ghost" data-expand="1">Expand</button><button type="button" class="ghost" data-expand="0">Collapse</button>';

VIEWS.trial = async () => {
  const tb = await api(C(`/reports/trial-balance?to=${S.to}`));
  const rows = [];
  walk(tb.groups, (n, d, p) => {
    const a = num(n.amount);
    rows.push(`<tr data-path="${p}" data-depth="${d}" class="${n.kind} d${d}">${nameCell(n, d, p)}
      <td class="num">${a > 0 ? fm(a) : ''}</td><td class="num">${a < 0 ? fm(-a) : ''}</td></tr>`);
  });
  const diff = num(tb.opening_difference);
  main().innerHTML = `
  ${reportHead('Trial Balance', `Closing balances as of ${fdate(tb.as_of)}`, expandButtons)}
  <div class="card flush"><div class="tblwrap"><table class="tbl tree">
    <thead><tr><th>Particulars</th><th class="num">Debit</th><th class="num">Credit</th></tr></thead>
    <tbody>${rows.join('') || '<tr><td colspan="3" class="muted">No balances yet.</td></tr>'}
    ${diff ? `<tr class="line warnrow"><td class="name" style="--d:0"><span class="caret-sp"></span>Difference in opening balances</td><td class="num">${diff < 0 ? fm(-diff) : ''}</td><td class="num">${diff > 0 ? fm(diff) : ''}</td></tr>` : ''}</tbody>
    <tfoot><tr><td>Grand total</td><td class="num">${fm(tb.grand_total)}</td><td class="num">${fm(tb.grand_total)}</td></tr></tfoot>
  </table></div></div>`;
  bindReport();
};

function sideTable(title, nodes, total, prefix) {
  const rows = [];
  walk(nodes, (n, d, p) => {
    rows.push(`<tr data-path="${prefix}${p}" data-depth="${d}" class="${n.kind} d${d}">${nameCell(n, d, `${prefix}${p}`)}
      <td class="num sub">${d > 0 ? fb(n.amount) : ''}</td><td class="num">${d === 0 ? fm(n.amount) : ''}</td></tr>`);
  });
  return `<table class="tbl tree tside">
    <thead><tr><th>${title}</th><th></th><th class="num">Amount</th></tr></thead>
    <tbody>${rows.join('')}</tbody>
    <tfoot><tr><td>Total</td><td></td><td class="num">${fm(total)}</td></tr></tfoot></table>`;
}

// Each side prefixes its row paths so the two trees collapse independently.
function sidePair(lt, ln, rt, rn, total, key) {
  return `<div class="tpair">
    <div>${sideTable(lt, ln, total, `${key}L`)}</div>
    <div>${sideTable(rt, rn, total, `${key}R`)}</div></div>`;
}

VIEWS.pl = async () => {
  const r = await api(C(`/reports/profit-loss?${period()}`));
  main().innerHTML = `
  ${reportHead('Profit & Loss', `${fdate(r.from)} to ${fdate(r.to)}`, expandButtons)}
  <div class="card flush">
    <h2 class="sect">Trading account</h2>
    ${sidePair('Particulars (Dr)', r.trading.debit, 'Particulars (Cr)', r.trading.credit, r.trading_total, 't')}
    <h2 class="sect">Profit &amp; loss account</h2>
    ${sidePair('Particulars (Dr)', r.pnl.debit, 'Particulars (Cr)', r.pnl.credit, r.pnl_total, 'p')}
  </div>`;
  bindReport();
};

VIEWS.bs = async () => {
  const r = await api(C(`/reports/balance-sheet?to=${S.to}`));
  main().innerHTML = `
  ${reportHead('Balance Sheet', `As of ${fdate(r.as_of)}`, expandButtons)}
  ${r.balanced ? '' : '<p class="err">The balance sheet does not tally. Please report this as a bug.</p>'}
  <div class="card flush">${sidePair('Liabilities', r.liabilities, 'Assets', r.assets, r.assets_total, 'b')}</div>`;
  bindReport();
};

VIEWS.ledger = async (arg) => {
  const id = arg ? +arg : null;
  const picker = `<div class="picker noprint"><input list="dl-all" id="lpick" placeholder="Choose a ledger" value="${esc(id ? S.ledgers.find((l) => l.id === id)?.name : '')}" data-search>
    <datalist id="dl-all">${S.ledgers.map((l) => `<option value="${esc(l.name)}">${esc(l.group_name)}</option>`).join('')}</datalist></div>`;
  if (!id) {
    main().innerHTML = `${reportHead('Ledger statement', 'Pick a ledger to see its vouchers', picker)}`;
    bindPicker();
    $('#lpick').focus();
    return;
  }
  const r = await api(C(`/reports/ledger/${id}?${period()}`));
  main().innerHTML = `
  ${reportHead(esc(r.ledger.name), `Ledger statement, ${fdate(r.from)} to ${fdate(r.to)}`, picker)}
  <div class="card flush"><div class="tblwrap"><table class="tbl click">
    <thead><tr><th>Date</th><th>Particulars</th><th>Type</th><th>No.</th><th class="num">Debit</th><th class="num">Credit</th><th class="num">Balance</th></tr></thead>
    <tbody><tr class="line"><td></td><td>Opening balance</td><td></td><td></td><td></td><td></td><td class="num">${drcr(r.opening) || '0.00'}</td></tr>
    ${r.rows.map((x) => `<tr data-href="#/c/${S.cid}/voucher/edit-${x.voucher_id}"><td class="nowrap">${fdate(x.date)}</td>
      <td>${esc(x.particulars)}${x.narration ? `<small class="muted block">${esc(x.narration)}</small>` : ''}</td>
      <td>${esc(x.vtype)}</td><td>${esc(x.number)}</td><td class="num">${fb(x.debit)}</td><td class="num">${fb(x.credit)}</td><td class="num">${drcr(x.balance)}</td></tr>`).join('')}</tbody>
    <tfoot><tr><td></td><td>Totals / closing balance</td><td></td><td></td><td class="num">${fm(r.debit_total)}</td><td class="num">${fm(r.credit_total)}</td><td class="num">${drcr(r.closing) || '0.00'}</td></tr></tfoot>
  </table></div></div>`;
  bindPicker();
  bindReport();
};

function bindPicker() {
  $('#lpick').addEventListener('change', (e) => {
    const l = ledgerByName(e.target.value);
    if (l) go('ledger', l.id);
  });
}

VIEWS.stock = async () => {
  const r = await api(C(`/reports/stock-summary?${period()}`));
  main().innerHTML = `
  ${reportHead('Stock Summary', `${fdate(r.from)} to ${fdate(r.to)} &middot; weighted average cost`)}
  <div class="card flush"><div class="tblwrap"><table class="tbl">
    <thead><tr><th rowspan="2">Item</th><th colspan="2" class="c">Opening</th><th colspan="2" class="c">Inwards</th><th colspan="2" class="c">Outwards</th><th colspan="3" class="c">Closing</th></tr>
    <tr><th class="num">Qty</th><th class="num">Value</th><th class="num">Qty</th><th class="num">Value</th><th class="num">Qty</th><th class="num">Value</th><th class="num">Qty</th><th class="num">Rate</th><th class="num">Value</th></tr></thead>
    <tbody>${r.items.map((i) => `<tr><td>${esc(i.name)}</td>
      <td class="num">${fq(i.opening_qty)} <small class="muted">${num(i.opening_qty) ? esc(i.unit) : ''}</small></td><td class="num">${fb(i.opening_value)}</td>
      <td class="num">${fq(i.in_qty)}</td><td class="num">${fb(i.in_value)}</td>
      <td class="num">${fq(i.out_qty)}</td><td class="num">${fb(i.out_value)}</td>
      <td class="num">${fq(i.closing_qty)} <small class="muted">${esc(i.unit)}</small></td><td class="num">${fb(i.closing_rate)}</td><td class="num">${fm(i.closing_value)}</td></tr>`).join('')
      || '<tr><td colspan="10" class="muted">No stock items yet.</td></tr>'}</tbody>
    <tfoot><tr><td>Total</td><td></td><td class="num">${fm(r.opening_total)}</td><td colspan="6"></td><td class="num">${fm(r.closing_total)}</td></tr></tfoot>
  </table></div></div>
  <p class="muted note">Outward value is at selling price. Closing value is quantity in hand at weighted average purchase cost.</p>`;
  bindReport();
};

VIEWS.gst = async () => {
  const r = await api(C(`/reports/gst?${period()}`));
  const taxCells = (x) => `<td class="num">${fb(x.taxable)}</td><td class="num">${fb(x.cgst)}</td><td class="num">${fb(x.sgst)}</td><td class="num">${fb(x.igst)}</td><td class="num">${fm(x.total)}</td>`;
  const taxHead = '<th class="num">Taxable</th><th class="num">CGST</th><th class="num">SGST</th><th class="num">IGST</th><th class="num">Total</th>';
  const rateTable = (title, rows, total) => `
    <section class="card flush"><h2 class="sect">${title}</h2><div class="tblwrap"><table class="tbl">
      <thead><tr><th>GST rate</th>${taxHead}</tr></thead>
      <tbody>${rows.map((x) => `<tr><td>${esc(x.rate)}%</td>${taxCells(x)}</tr>`).join('') || '<tr><td colspan="6" class="muted">Nothing in this period.</td></tr>'}</tbody>
      <tfoot><tr><td>Total</td>${taxCells({ ...total, total: num(total.taxable) + num(total.cgst) + num(total.sgst) + num(total.igst) })}</tr></tfoot>
    </table></div></section>`;
  const net = (k) => {
    const v = num(r.net_tax[k]);
    return `<div class="tile"><span class="muted">${k.toUpperCase()}</span><strong>${fm(Math.abs(v))}</strong><small class="muted">${v > 0 ? 'payable' : v < 0 ? 'credit (ITC) carried' : 'nil'}</small></div>`;
  };
  main().innerHTML = `
  ${reportHead('GST Summary', `${fdate(r.from)} to ${fdate(r.to)}`)}
  ${S.company.gst_registered ? '' : '<p class="note">This company has no GSTIN, so invoices carry no GST.</p>'}
  <div class="tiles">${net('cgst')}${net('sgst')}${net('igst')}</div>
  <p class="muted note">Output tax minus input tax per head, before cross-utilisation. This summary helps you prepare returns; Bahi does not file them.</p>
  ${rateTable('Outward supplies (sales less credit notes)', r.outward_by_rate, r.outward_total)}
  ${rateTable('Inward supplies (purchases less debit notes)', r.inward_by_rate, r.inward_total)}
  <section class="card flush"><h2 class="sect">B2B invoices</h2><div class="tblwrap"><table class="tbl">
    <thead><tr><th>Date</th><th>No.</th><th>Party</th><th>GSTIN</th><th>POS</th>${taxHead}</tr></thead>
    <tbody>${r.b2b.map((x) => `<tr><td class="nowrap">${fdate(x.date)}</td><td>${esc(x.number)}</td><td>${esc(x.party)}</td><td class="mono">${esc(x.gstin)}</td><td>${esc(x.place_of_supply)}</td>${taxCells(x)}</tr>`).join('') || '<tr><td colspan="10" class="muted">None.</td></tr>'}</tbody>
  </table></div></section>
  <section class="card flush"><h2 class="sect">B2C summary</h2><div class="tblwrap"><table class="tbl">
    <thead><tr><th>Place of supply</th><th>Rate</th>${taxHead}</tr></thead>
    <tbody>${r.b2c.map((x) => `<tr><td>${esc(x.place_of_supply)} ${esc(x.state_name)}</td><td>${esc(x.rate)}%</td>${taxCells(x)}</tr>`).join('') || '<tr><td colspan="7" class="muted">None.</td></tr>'}</tbody>
  </table></div></section>
  <section class="card flush"><h2 class="sect">HSN summary (outward)</h2><div class="tblwrap"><table class="tbl">
    <thead><tr><th>HSN/SAC</th><th>Rate</th><th class="num">Qty</th>${taxHead}</tr></thead>
    <tbody>${r.hsn.map((x) => `<tr><td class="mono">${esc(x.hsn)}</td><td>${esc(x.rate)}%</td><td class="num">${fq(x.qty)}</td>${taxCells(x)}</tr>`).join('') || '<tr><td colspan="8" class="muted">None.</td></tr>'}</tbody>
  </table></div></section>`;
  bindReport();
};

// ----------------------------------------------------------------- masters
function masterLayout(title, listHtml, formHtml, count) {
  return `<div class="masters">
    <section class="card flush mlist">
      <div class="listhead"><h1>${title} <span class="muted">${count}</span></h1>
        <input type="search" data-search placeholder="Search  /" aria-label="Search">
        <button type="button" data-newbtn>New <kbd>N</kbd></button></div>
      <div class="tblwrap">${listHtml}</div>
    </section>
    <section class="card mform">${formHtml}</section>
  </div>`;
}

function bindMaster(view, form, onSubmit, onDelete) {
  const search = $('[data-search]');
  search.addEventListener('input', () => {
    const q = lower(search.value);
    $$('.mlist tbody tr').forEach((tr) => { tr.hidden = q && !lower(tr.innerText).includes(q); });
  });
  $('[data-newbtn]').onclick = () => go(view);
  $$('.mlist tr[data-id]').forEach((tr) => { tr.onclick = () => go(view, tr.dataset.id); });
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    try { await onSubmit(Object.fromEntries(new FormData(form))); } catch (err) { $('.err', form).textContent = err.message; fail(err); }
  });
  $('[data-delete]', form)?.addEventListener('click', async () => {
    if (!confirm('Delete this record?')) return;
    try { await onDelete(); } catch (err) { fail(err); }
  });
  setTimeout(() => form.querySelector('input:not([type=hidden]), select')?.focus(), 0);
}

const formFoot = (editing) => `<div class="err"></div><div class="vfoot">
  ${editing ? '<button type="button" class="ghost danger" data-delete>Delete</button>' : ''}<button class="primary">${editing ? 'Save changes' : 'Create'}</button></div>`;

const groupOptions = (sel, exclude) => [...S.groups].filter((g) => g.id !== exclude)
  .map((g) => `<option value="${g.id}" ${g.id === sel ? 'selected' : ''}>${esc(g.name)}${g.parent_name ? '' : ' (primary)'}</option>`).join('');

VIEWS.ledgers = async (arg) => {
  await loadMasters();
  const cur = arg ? S.ledgers.find((l) => l.id === +arg) : null;
  const list = `<table class="tbl click"><thead><tr><th>Name</th><th>Group</th><th class="num">Balance</th></tr></thead>
    <tbody>${S.ledgers.map((l) => `<tr data-id="${l.id}" class="${cur?.id === l.id ? 'sel' : ''}"><td>${esc(l.name)}</td><td class="muted">${esc(l.group_name)}</td>
    <td class="num">${drcr(signed(l.closing_balance, l.closing_side))}</td></tr>`).join('')}</tbody></table>`;
  const L = cur || { name: '', group_id: S.groups.find((g) => g.name === 'Sundry Debtors')?.id, opening_balance: '', opening_side: 'dr', gstin: '', state_code: '', address: '', tax_type: '', tax_direction: '' };
  const form = `<form id="mform" autocomplete="off">
    <h2>${cur ? `Alter ledger` : 'New ledger'}</h2>
    <label>Name<input name="name" value="${esc(L.name)}" required></label>
    <label>Under group<select name="group_id">${groupOptions(L.group_id)}</select></label>
    <div class="row2"><label>Opening balance<input name="opening_balance" value="${num(L.opening_balance) ? esc(L.opening_balance) : ''}" inputmode="decimal" class="num"></label>
      <label>Dr / Cr<select name="opening_side"><option value="dr" ${L.opening_side === 'dr' ? 'selected' : ''}>Dr</option><option value="cr" ${L.opening_side === 'cr' ? 'selected' : ''}>Cr</option></select></label></div>
    <fieldset class="party"><legend>Party details</legend>
      <label>GSTIN<input name="gstin" value="${esc(L.gstin)}" maxlength="15" class="caps"></label>
      <label>State<select name="state_code">${stateOptions(L.state_code, true)}</select></label>
      <label>Address<textarea name="address" rows="2">${esc(L.address)}</textarea></label></fieldset>
    <fieldset class="tax"><legend>Tax ledger</legend>
      <div class="row2"><label>Tax type<select name="tax_type"><option value="">None</option>${['cgst', 'sgst', 'igst'].map((t) => `<option value="${t}" ${L.tax_type === t ? 'selected' : ''}>${t.toUpperCase()}</option>`).join('')}</select></label>
      <label>Direction<select name="tax_direction"><option value="">-</option><option value="output" ${L.tax_direction === 'output' ? 'selected' : ''}>Output (sales)</option><option value="input" ${L.tax_direction === 'input' ? 'selected' : ''}>Input (purchases)</option></select></label></div></fieldset>
    ${cur ? `<p><a href="#/c/${S.cid}/ledger/${cur.id}">View statement</a></p>` : ''}
    ${formFoot(!!cur)}</form>`;
  main().innerHTML = masterLayout('Ledgers', list, form, S.ledgers.length);
  const f = $('#mform');
  const sync = () => {
    const gid = +f.group_id.value;
    f.querySelector('.party').hidden = !(underGroup(gid, 'Sundry Debtors') || underGroup(gid, 'Sundry Creditors') || underGroup(gid, 'Bank Accounts'));
    const tax = f.querySelector('.tax');
    tax.hidden = !underGroup(gid, 'Duties & Taxes');
    if (tax.hidden) { f.tax_type.value = ''; f.tax_direction.value = ''; }
  };
  f.group_id.onchange = sync;
  sync();
  bindMaster('ledgers', f, async (d) => {
    const body = { ...d, group_id: +d.group_id, opening_balance: d.opening_balance || '0', tax_type: d.tax_type || null, tax_direction: d.tax_direction || null };
    if (cur) await api(C(`/ledgers/${cur.id}`), { method: 'PUT', body });
    else await api(C('/ledgers'), { method: 'POST', body });
    toast(`Ledger ${d.name} saved`);
    go('ledgers', cur?.id);
  }, async () => {
    await api(C(`/ledgers/${cur.id}`), { method: 'DELETE' });
    toast('Ledger deleted');
    go('ledgers');
  });
};

VIEWS.groups = async (arg) => {
  await loadMasters();
  const cur = arg ? S.groups.find((g) => g.id === +arg) : null;
  const list = `<table class="tbl click"><thead><tr><th>Name</th><th>Under</th><th>Nature</th></tr></thead>
    <tbody>${S.groups.map((g) => `<tr data-id="${g.id}" class="${cur?.id === g.id ? 'sel' : ''}"><td>${esc(g.name)}${g.reserved ? '' : ' <span class="tag">custom</span>'}</td>
    <td class="muted">${esc(g.parent_name || 'Primary')}</td><td class="muted">${esc(g.nature)}${g.affects_gross_profit ? ', trading' : ''}</td></tr>`).join('')}</tbody></table>`;
  const ro = cur?.reserved;
  const form = `<form id="mform" autocomplete="off">
    <h2>${cur ? (ro ? 'Built-in group' : 'Alter group') : 'New group'}</h2>
    ${ro ? '<p class="muted">Built-in groups drive the balance sheet and P&amp;L, so they cannot be changed. Create a sub-group under one instead.</p>' : ''}
    <label>Name<input name="name" value="${esc(cur?.name || '')}" required ${ro ? 'disabled' : ''}></label>
    <label>Under<select name="parent_id" ${ro ? 'disabled' : ''}>${groupOptions(cur?.parent_id ?? S.groups.find((g) => g.name === 'Indirect Expenses')?.id, cur?.id)}</select></label>
    ${ro ? '' : formFoot(!!cur)}</form>`;
  main().innerHTML = masterLayout('Groups', list, form, S.groups.length);
  bindMaster('groups', $('#mform'), async (d) => {
    const body = { name: d.name, parent_id: +d.parent_id };
    if (cur) await api(C(`/groups/${cur.id}`), { method: 'PUT', body });
    else await api(C('/groups'), { method: 'POST', body });
    toast(`Group ${d.name} saved`);
    go('groups', cur?.id);
  }, async () => {
    await api(C(`/groups/${cur.id}`), { method: 'DELETE' });
    toast('Group deleted');
    go('groups');
  });
};

VIEWS.items = async (arg) => {
  await loadMasters();
  const cur = arg ? S.items.find((i) => i.id === +arg) : null;
  const list = `<table class="tbl click"><thead><tr><th>Name</th><th>Unit</th><th>HSN</th><th class="num">GST</th><th class="num">Opening qty</th><th class="num">Opening value</th></tr></thead>
    <tbody>${S.items.map((i) => `<tr data-id="${i.id}" class="${cur?.id === i.id ? 'sel' : ''}"><td>${esc(i.name)}</td><td class="muted">${esc(i.unit)}</td>
    <td class="mono">${esc(i.hsn)}</td><td class="num">${esc(i.gst_rate)}%</td><td class="num">${fq(i.opening_qty)}</td><td class="num">${fb(i.opening_value)}</td></tr>`).join('')
    || '<tr><td colspan="6" class="muted">No stock items yet.</td></tr>'}</tbody></table>`;
  const I = cur || { name: '', unit_id: S.units[0]?.id, hsn: '', gst_rate: '', opening_qty: '', opening_value: '' };
  const form = `<form id="mform" autocomplete="off">
    <h2>${cur ? 'Alter stock item' : 'New stock item'}</h2>
    <label>Name<input name="name" value="${esc(I.name)}" required></label>
    <div class="row2"><label>Unit<select name="unit_id">${S.units.map((u) => `<option value="${u.id}" ${u.id === I.unit_id ? 'selected' : ''}>${esc(u.symbol)} (${esc(u.name)})</option>`).join('')}</select></label>
      <label>HSN/SAC<input name="hsn" value="${esc(I.hsn)}" class="mono"></label></div>
    <label>GST rate %<input name="gst_rate" value="${num(I.gst_rate) ? esc(I.gst_rate) : ''}" inputmode="decimal" class="num" placeholder="0"></label>
    <div class="row2"><label>Opening quantity<input name="opening_qty" value="${num(I.opening_qty) ? esc(I.opening_qty) : ''}" inputmode="decimal" class="num"></label>
      <label>Opening value<input name="opening_value" value="${num(I.opening_value) ? esc(I.opening_value) : ''}" inputmode="decimal" class="num"></label></div>
    ${formFoot(!!cur)}</form>
    <form id="uform" class="unitform" autocomplete="off"><h3>Add a unit</h3>
      <div class="row2"><label>Symbol<input name="symbol" required class="sm"></label><label>Name<input name="name"></label></div>
      <button>Add unit</button></form>`;
  main().innerHTML = masterLayout('Stock Items', list, form, S.items.length);
  bindMaster('items', $('#mform'), async (d) => {
    const body = { ...d, unit_id: +d.unit_id, gst_rate: d.gst_rate || '0', opening_qty: d.opening_qty || '0', opening_value: d.opening_value || '0' };
    if (cur) await api(C(`/items/${cur.id}`), { method: 'PUT', body });
    else await api(C('/items'), { method: 'POST', body });
    toast(`Item ${d.name} saved`);
    go('items', cur?.id);
  }, async () => {
    await api(C(`/items/${cur.id}`), { method: 'DELETE' });
    toast('Item deleted');
    go('items');
  });
  $('#uform').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      await api(C('/units'), { method: 'POST', body: Object.fromEntries(new FormData(e.target)) });
      toast('Unit added');
      route();
    } catch (err) { fail(err); }
  });
};

VIEWS.company = async () => {
  S.company = await api(C('/company'));
  const c = S.company;
  main().innerHTML = `
  <div class="rhead"><div><h1>Company</h1><p class="muted">Data file id: <code>${esc(S.cid)}</code></p></div></div>
  <div class="split">
    <form id="coform" class="card form" autocomplete="off">
      <h2>Details</h2>
      <label>Name<input name="name" value="${esc(c.name)}" required></label>
      <div class="row2"><label>State<select name="state_code">${stateOptions(c.state_code)}</select></label>
        <label>Books begin from<input type="date" name="books_from" value="${c.books_from}"></label></div>
      <label>GSTIN<input name="gstin" value="${esc(c.gstin)}" maxlength="15" class="caps"></label>
      <label>Address<textarea name="address" rows="3">${esc(c.address)}</textarea></label>
      <div class="err"></div><button class="primary">Save</button>
    </form>
    <div class="card">
      <h2>Backup</h2>
      <p class="muted">Download everything in this company as one JSON file. Restore it from the company list.</p>
      <a class="btn" href="/api/c/${esc(S.cid)}/export" download>Download backup</a>
      <h2 class="mt">Switch</h2>
      <a class="btn" href="#/">Open another company</a>
      <h2 class="mt">Danger zone</h2>
      <p class="muted">Deleting removes the company file from this computer.</p>
      <button type="button" class="danger" id="codel">Delete company</button>
    </div>
  </div>`;
  $('#coform').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      S.company = await api(C('/company'), { method: 'PUT', body: Object.fromEntries(new FormData(e.target)) });
      $('#co-name').innerHTML = `<strong>${esc(S.company.name)}</strong><span>${esc(S.company.gstin || 'GST unregistered')}</span>`;
      toast('Company saved');
    } catch (err) { $('#coform .err').textContent = err.message; }
  });
  $('#codel').onclick = async () => {
    const typed = prompt(`Type the company name (${c.name}) to delete it permanently. Download a backup first if unsure.`);
    if (typed !== c.name) return;
    try { await api(`/companies/${S.cid}`, { method: 'DELETE' }); S.company = null; location.hash = '#/'; } catch (err) { fail(err); }
  };
};

// -------------------------------------------------------------------- print
VIEWS.print = async (arg) => {
  const v = await api(C(`/vouchers/${arg}`));
  const co = v.company;
  const inv = isInv(v.vtype);
  const title = { Sales: 'Tax Invoice', Purchase: 'Purchase Voucher', 'Credit Note': 'Credit Note', 'Debit Note': 'Debit Note' }[v.vtype] || `${v.vtype} Voucher`;
  const igst = num(v.totals.igst) > 0;
  const body = inv ? `
    <div class="doc-parties">
      <div><small class="muted">${salesSide(v.vtype) ? 'Bill to' : 'Supplier'}</small><strong>${esc(v.party?.name)}</strong>
        <span>${esc(v.party?.address)}</span>${v.party?.gstin ? `<span>GSTIN ${esc(v.party.gstin)}</span>` : ''}
        ${v.party?.state_name ? `<span>State ${esc(v.party.state_code)} ${esc(v.party.state_name)}</span>` : ''}</div>
      <div><small class="muted">Place of supply</small><strong>${esc(v.place_of_supply)} ${esc(v.place_of_supply_name)}</strong>
        ${v.reference ? `<small class="muted">Reference</small><span>${esc(v.reference)}</span>` : ''}</div>
    </div>
    <table class="tbl doc-lines">
      <thead><tr><th>#</th><th>Description</th><th>HSN/SAC</th><th class="num">Qty</th><th class="num">Rate</th><th class="num">Taxable</th><th class="num">GST</th>
      ${igst ? '<th class="num">IGST</th>' : '<th class="num">CGST</th><th class="num">SGST</th>'}<th class="num">Total</th></tr></thead>
      <tbody>${v.lines.map((l, i) => `<tr><td>${i + 1}</td><td>${esc(l.description)}</td><td class="mono">${esc(l.hsn)}</td>
        <td class="num">${l.qty ? `${fq(l.qty)} ${esc(l.unit)}` : ''}</td><td class="num">${l.rate ? fm(l.rate) : ''}</td><td class="num">${fm(l.taxable)}</td><td class="num">${esc(l.gst_rate)}%</td>
        ${igst ? `<td class="num">${fb(l.igst)}</td>` : `<td class="num">${fb(l.cgst)}</td><td class="num">${fb(l.sgst)}</td>`}<td class="num">${fm(l.total)}</td></tr>`).join('')}</tbody>
    </table>
    <div class="doc-sum"><table class="tbl sums">
      <tr><td>Taxable value</td><td class="num">${fm(v.totals.taxable)}</td></tr>
      ${num(v.totals.cgst) ? `<tr><td>CGST</td><td class="num">${fm(v.totals.cgst)}</td></tr><tr><td>SGST</td><td class="num">${fm(v.totals.sgst)}</td></tr>` : ''}
      ${igst ? `<tr><td>IGST</td><td class="num">${fm(v.totals.igst)}</td></tr>` : ''}
      ${num(v.totals.round_off) ? `<tr><td>Round off</td><td class="num">${fm(v.totals.round_off)}</td></tr>` : ''}
      <tr class="grand"><td>Total</td><td class="num">${fm(v.amount)}</td></tr></table></div>` : `
    <table class="tbl doc-lines"><thead><tr><th>Particulars</th><th class="num">Debit</th><th class="num">Credit</th></tr></thead>
      <tbody>${v.entries.map((e) => `<tr><td>${esc(e.ledger_name)}</td><td class="num">${e.side === 'dr' ? fm(e.amount) : ''}</td><td class="num">${e.side === 'cr' ? fm(e.amount) : ''}</td></tr>`).join('')}</tbody>
      <tfoot><tr><td>Total</td><td class="num">${fm(v.amount)}</td><td class="num">${fm(v.amount)}</td></tr></tfoot></table>`;
  main().innerHTML = `
  <div class="rhead noprint"><div><h1>${esc(v.vtype)} ${esc(v.number)}</h1></div>
    <div class="ractions"><a class="btn ghost" href="#/c/${S.cid}/voucher/edit-${v.id}">Alter</a><button type="button" class="primary" onclick="print()">Print</button></div></div>
  <article class="doc card">
    <header class="doc-head">
      <div><h2>${esc(co.name)}</h2><span>${esc(co.address)}</span>${co.gstin ? `<span>GSTIN ${esc(co.gstin)}</span>` : ''}<span>State ${esc(co.state_code)} ${esc(co.state_name)}</span></div>
      <div class="doc-title"><h3>${title}</h3><span>No. <strong>${esc(v.number)}</strong></span><span>Date <strong>${fdate(v.date)}</strong></span></div>
    </header>
    ${body}
    <p class="words"><small class="muted">Amount in words</small> ${esc(v.amount_in_words)}</p>
    ${v.narration ? `<p><small class="muted">Narration</small> ${esc(v.narration)}</p>` : ''}
    <footer class="doc-foot"><span class="muted">Computer generated with Bahi</span><span>For ${esc(co.name)}<br><br><br>Authorised signatory</span></footer>
  </article>`;
};

// --------------------------------------------------------------- keyboard
const editable = (el) => el && (el.isContentEditable || ['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName));

document.addEventListener('keydown', (e) => {
  const help = $('#help');
  if (e.key === 'Escape' && !help.hidden) { help.hidden = true; return; }
  if (!S.cid) return;
  if (/^F[4-9]$/.test(e.key)) {
    const t = FKEYS[`${e.ctrlKey || e.metaKey ? 'Ctrl+' : ''}${e.key}`];
    if (t) {
      e.preventDefault();
      if (S.view === 'voucher' && V && !V.id) switchType(t);
      else go('voucher', encodeURIComponent(t));
    }
    return;
  }
  if ((e.ctrlKey || e.metaKey) && (e.key === 'Enter' || e.key === 's') && $('#vform')) {
    e.preventDefault();
    $('#vform').requestSubmit();
    return;
  }
  if (e.key === 'Escape') {
    if (editable(document.activeElement)) document.activeElement.blur();
    else if (S.view !== 'home') go('home');
    return;
  }
  if (editable(e.target) || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === '?') { renderHelp(); help.hidden = false; return; }
  if (e.key === '/') { const s = $('[data-search]'); if (s) { e.preventDefault(); s.focus(); } return; }
  if (lower(e.key) === 'n' && $('[data-newbtn]')) { e.preventDefault(); $('[data-newbtn]').click(); return; }
  const item = NAV.find((n) => n.key && lower(n.key) === lower(e.key));
  if (item) { e.preventDefault(); go(item.view); }
});

document.addEventListener('click', (e) => {
  const tr = e.target.closest('tr[data-href]');
  if (tr && !e.target.closest('a,button')) location.hash = tr.dataset.href.slice(1);
});

$('#help-btn').onclick = () => { renderHelp(); $('#help').hidden = false; };
$('#help').onclick = (e) => { if (e.target.id === 'help') e.currentTarget.hidden = true; };
$('#p-from').addEventListener('change', setPeriod);
$('#p-to').addEventListener('change', setPeriod);
window.addEventListener('hashchange', route);
route();
