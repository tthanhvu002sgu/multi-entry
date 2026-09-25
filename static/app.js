const $ = id => document.getElementById(id);
const form = $('plan-form');
let context = null, result = null, revision = 0, contextVersion = 0;
let stopMeta = {sl_mode:'manual',swing_time:null,swing_price:null};
let stopBusy = false;
let swingEnabled = false, queuedSwing = false;
let allSymbols = [], activeSymbols = [];
const fmt = (n, digits = 2) => Number(n).toLocaleString('en-US', {minimumFractionDigits: digits, maximumFractionDigits: digits});
const money = n => `${fmt(n)} ${context?.account.currency || ''}`;
function message(text) { $('error').textContent = text; $('error').hidden = !text; }
async function api(path, options = {}) {
  const response = await fetch(path, {credentials: 'same-origin', ...options, headers: {'Content-Type': 'application/json', ...options.headers}});
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401) showLogin();
    throw new Error(typeof data.detail === 'string' ? data.detail : 'Thông tin chưa hợp lệ. Kiểm tra giá, số entry và ngân sách.');
  }
  return data;
}
function showLogin() { $('login-panel').hidden = false; $('workspace').hidden = true; $('saved-panel').hidden = true; $('connection').textContent = 'Cần đăng nhập'; }
function invalidate() { revision++; result = null; document.querySelector('.results').classList.add('stale'); $('verdict').textContent = 'CẦN TÍNH LẠI'; $('save').disabled = true; $('export').disabled = true; }
function data() {
  const values = Object.fromEntries(new FormData(form));
  for (const key of ['entry','stop','count','budget','lot','commission','reserve','buffer_ticks','step_ticks']) values[key] = Number(values[key]);
  return {...values,...stopMeta};
}
function stopControls() {
  const s = context?.symbol;
  const ready = s && s.name === $('symbol').value;
  $('sl-up').disabled = $('sl-down').disabled = stopBusy || !ready;
  $('get-swing').disabled = stopBusy;
  if (ready) {
    const ticks = Number($('step-ticks').value);
    $('step-hint').textContent = `1 tick ${s.name} = ${s.tick_size.toFixed(s.digits)} · Mỗi lần ▲/▼: ${(s.tick_size*ticks).toFixed(s.digits)} (${ticks} ticks)`;
  } else $('step-hint').textContent = 'Đang chờ thông số symbol…';
  let source = stopMeta.sl_mode === 'swing' ? 'SL theo swing' : stopMeta.sl_mode === 'adjusted' ? 'SL đã chỉnh tay · giữ cố định' : 'SL nhập tay · giữ cố định';
  if (stopMeta.swing_time) source += ` · mốc ${Number(stopMeta.swing_price).toFixed(s?.digits ?? 5)} · ${$('timeframe').value} · ${new Date(stopMeta.swing_time*1000).toLocaleString('vi-VN')}`;
  $('sl-source').textContent = source;
}
async function changeStop(action) {
  if (stopBusy) { if (action === 'swing') queuedSwing = true; return; }
  queuedSwing = false;
  if (!$('entry').value || !$('entry').reportValidity()) { message('Nhập entry đầu hoặc dùng giá hiện tại trước khi lấy/chỉnh SL.'); return; }
  if (action !== 'swing' && (!$('stop').value || !$('stop').reportValidity())) return;
  if (action === 'swing' && !$('buffer-ticks').reportValidity()) return;
  invalidate(); message(''); const version = revision;
  if (action === 'swing') {
    swingEnabled = true;
    $('stop').value = '';
    stopMeta = {sl_mode:'manual',swing_time:null,swing_price:null};
  } else swingEnabled = false;
  stopBusy = true; stopControls();
  try {
    const base = {symbol:$('symbol').value,side:form.elements.side.value,entry:Number($('entry').value)};
    const payload = action === 'swing' ? {...base,timeframe:$('timeframe').value,buffer_ticks:Number($('buffer-ticks').value)} : {...base,stop:Number($('stop').value),direction:action,ticks:Number($('step-ticks').value)};
    const response = await api(action === 'swing' ? '/api/swing' : '/api/stop-step',{method:'POST',body:JSON.stringify(payload)});
    if (version !== revision) return;
    context = response.context;
    $('stop').value = response.stop.toFixed(context.symbol.digits);
    if (action === 'swing') stopMeta = {sl_mode:'swing',swing_time:response.swing_time,swing_price:response.swing_price};
    else stopMeta.sl_mode = 'adjusted';
    stopControls();
    await compute();
  } catch(error) { if (version === revision) message(error.message); }
  finally { stopBusy = false; stopControls(); if (queuedSwing && swingEnabled) { queuedSwing=false; changeStop('swing'); } }
}
function renderQuickTickers() {
  const container = $('quick-tickers');
  if (!container) return;
  container.replaceChildren(...activeSymbols.map(sym => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `ticker-pill${sym === $('symbol').value ? ' active' : ''}`;
    btn.textContent = sym;
    btn.onclick = () => {
      if ($('symbol').value !== sym) {
        $('symbol').value = sym;
        $('symbol').dispatchEvent(new Event('change'));
      }
    };
    return btn;
  }));
}

function updateSymbolSelect(preferred) {
  const target = preferred || $('symbol').value;
  $('symbol').replaceChildren();
  if (!activeSymbols.length) {
    $('symbol').add(new Option('(Chưa có ticker nào)', ''));
    $('symbol').value = '';
    $('symbol').disabled = true;
  } else {
    $('symbol').disabled = false;
    activeSymbols.forEach(s => $('symbol').add(new Option(s, s)));
    if (activeSymbols.includes(target)) {
      $('symbol').value = target;
    } else {
      $('symbol').value = activeSymbols[0];
    }
  }
  renderQuickTickers();
}

function renderModalChips() {
  $('active-tickers-count').textContent = activeSymbols.length;
  $('no-active-tickers-msg').hidden = activeSymbols.length > 0;
  $('active-tickers-chips').replaceChildren(...activeSymbols.map(sym => {
    const chip = document.createElement('div');
    chip.className = 'ticker-chip';
    const txt = document.createElement('span');
    txt.textContent = sym;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'chip-remove';
    btn.setAttribute('aria-label', `Xóa ${sym}`);
    btn.textContent = '×';
    btn.onclick = () => removeTicker(sym);
    chip.append(txt, btn);
    return chip;
  }));
}

function updateAddSelect() {
  const query = $('add-ticker-search').value.trim().toUpperCase();
  const candidates = allSymbols.filter(s => !activeSymbols.includes(s) && (!query || s.toUpperCase().includes(query)));
  $('add-ticker-select').replaceChildren();
  if (!candidates.length) {
    $('add-ticker-select').add(new Option(allSymbols.length ? 'Không có ticker nào phù hợp' : 'Đang tải…', ''));
    $('add-ticker-select').disabled = true;
    $('btn-add-ticker').disabled = true;
  } else {
    $('add-ticker-select').disabled = false;
    $('btn-add-ticker').disabled = false;
    candidates.forEach(s => $('add-ticker-select').add(new Option(s, s)));
  }
}

function openTickerModal() {
  $('ticker-modal').hidden = false;
  $('add-ticker-search').value = '';
  $('add-ticker-msg').hidden = true;
  renderModalChips();
  updateAddSelect();
  $('add-ticker-search').focus();
}

function closeTickerModal() {
  $('ticker-modal').hidden = true;
}

async function addTicker(sym) {
  if (!sym) return;
  $('btn-add-ticker').disabled = true;
  $('add-ticker-msg').hidden = true;
  try {
    const res = await api('/api/symbols', {method: 'POST', body: JSON.stringify({symbol: sym})});
    activeSymbols = res.symbols;
    allSymbols = res.all_symbols;
    updateSymbolSelect(sym);
    renderModalChips();
    updateAddSelect();
    $('add-ticker-search').value = '';
    $('add-ticker-search').focus();
    $('symbol').dispatchEvent(new Event('change'));
  } catch (error) {
    $('add-ticker-msg').textContent = error.message;
    $('add-ticker-msg').hidden = false;
  } finally {
    $('btn-add-ticker').disabled = false;
  }
}

async function removeTicker(sym) {
  if (!sym) return;
  try {
    const res = await api(`/api/symbols/${encodeURIComponent(sym)}`, {method: 'DELETE'});
    activeSymbols = res.symbols;
    allSymbols = res.all_symbols;
    const current = $('symbol').value;
    const nextSym = current === sym ? (activeSymbols[0] || '') : current;
    updateSymbolSelect(nextSym);
    renderModalChips();
    updateAddSelect();
    if (current === sym) {
      if (nextSym) {
        $('symbol').dispatchEvent(new Event('change'));
      } else {
        context = null; invalidate(); $('empty').hidden = false; $('result').hidden = true; stopControls();
      }
    }
  } catch (error) {
    message(error.message);
  }
}

async function loadContext() {
  if (!$('symbol').value) return false;
  const version = ++contextVersion;
  const next = await api(`/api/context?symbol=${encodeURIComponent($('symbol').value)}`);
  if (version !== contextVersion) return false;
  context = next;
  const a = context.account, s = context.symbol;
  $('account').textContent = `${fmt(a.equity)} ${a.currency}`;
  $('account-meta').textContent = context.mode === 'online' ? `${a.server}` : `${a.server} · #${a.login}`;
  $('connection').textContent = context.mode === 'demo' ? '● MÔ PHỎNG' : (context.mode === 'online' ? '● TRỰC TUYẾN (LIVE)' : '● MT5 đã kết nối');
  $('notice').textContent = context.warnings.join(' '); $('notice').hidden = !context.warnings.length;
  document.querySelectorAll('.currency').forEach(el => el.textContent = a.currency);
  $('specs').textContent = `Min ${s.volume_min} · Bước ${s.volume_step} · Max ${s.volume_max} lot\nContract ${s.contract_size.toLocaleString('en-US')}`;
  $('quote').textContent = `Bid ${s.bid.toFixed(s.digits)} / Ask ${s.ask.toFixed(s.digits)}${context.quote_time ? ' · ' + new Date(context.quote_time * 1000).toLocaleTimeString('vi-VN') : ' · giả lập'}`;
  stopControls();
  renderQuickTickers();
  return true;
}

async function initialize() {
  try {
    const session = await api('/api/session');
    if (!session.authenticated) { showLogin(); return; }
    $('login-panel').hidden = true; $('workspace').hidden = false; $('saved-panel').hidden = false; $('logout').hidden = !session.required;
    const response = await api('/api/symbols');
    allSymbols = response.all_symbols || response.symbols;
    activeSymbols = response.symbols || [];
    updateSymbolSelect(activeSymbols.includes('EURUSD') ? 'EURUSD' : (activeSymbols[0] || ''));
    if (!allSymbols.length) throw new Error('Broker không có symbol Forex được hỗ trợ.');
    if (activeSymbols.length > 0) {
      await loadContext();
      if (session.mode === 'demo') await compute();
      else { $('entry').value = ''; $('stop').value = ''; }
    } else {
      openTickerModal();
    }
    await loadPlans();
  } catch (error) { message(error.message); $('connection').textContent = '● Chưa sẵn sàng'; }
}
let autoComputeTimer = null;

function canAutoCompute() {
  if (!context || !context.symbol) return false;
  const entryVal = Number($('entry').value);
  const stopVal = Number($('stop').value);
  if (!entryVal || !stopVal || entryVal <= 0 || stopVal <= 0) return false;
  const side = form.elements.side.value;
  if (side === 'buy' && stopVal >= entryVal) return false;
  if (side === 'sell' && stopVal <= entryVal) return false;
  const count = Number($('count').value);
  if (!count || count < 1 || count > 50) return false;
  if (form.elements.sizing.value === 'budget') {
    const budget = Number($('budget').value);
    if (!budget || budget <= 0) return false;
  } else {
    const lot = Number($('lot').value);
    if (!lot || lot <= 0) return false;
  }
  return true;
}

function scheduleAutoCompute(delay = 250) {
  if (autoComputeTimer) {
    clearTimeout(autoComputeTimer);
    autoComputeTimer = null;
  }
  if (!canAutoCompute()) return;
  if (delay === 0) {
    compute(null, true);
  } else {
    autoComputeTimer = setTimeout(() => {
      compute(null, true);
    }, delay);
  }
}

async function compute(event, isAuto = false) {
  event?.preventDefault();
  if (isAuto) {
    if (!canAutoCompute()) return;
  } else {
    if (!form.reportValidity()) return;
    const entryVal = Number($('entry').value);
    const stopVal = Number($('stop').value);
    const side = form.elements.side.value;
    if (side === 'buy' && stopVal >= entryVal) { message('Buy cần SL dưới entry đầu.'); return; }
    if (side === 'sell' && stopVal <= entryVal) { message('Sell cần SL trên entry đầu.'); return; }
  }
  message(''); invalidate();
  const version = revision;
  $('calculate').disabled = true; $('calculate').textContent = 'Đang tính…';
  try {
    const response = await api('/api/calculate', {method:'POST', body:JSON.stringify(data())});
    if (revision !== version) return;
    result = response; context = response.context; render();
  } catch(error) { if (revision === version) message(error.message); }
  finally { $('calculate').disabled = false; $('calculate').textContent = 'Tính kế hoạch →'; }
}
function render() {
  const a = context.account;
  $('account').textContent = `${fmt(a.equity)} ${a.currency}`;
  $('account-meta').textContent = `${a.server} · #${a.login}`;
  $('quote').textContent = `Bid ${context.symbol.bid.toFixed(context.symbol.digits)} / Ask ${context.symbol.ask.toFixed(context.symbol.digits)}${context.quote_time ? ' · ' + new Date(context.quote_time * 1000).toLocaleTimeString('vi-VN') : ' · giả lập'}`;
  $('connection').textContent = context.mode === 'demo' ? '● MÔ PHỎNG' : '● MT5 đã kết nối';
  document.querySelector('.results').classList.remove('stale');
  $('empty').hidden = true; $('result').hidden = false;
  $('save').disabled = false; $('export').disabled = false;
  document.querySelector('.risk-card').classList.toggle('over', !result.within_budget || !result.feasible);
  $('verdict').textContent = !result.feasible ? 'KHÔNG KHẢ THI' : result.within_budget ? 'TRONG NGÂN SÁCH' : 'VƯỢT NGÂN SÁCH';
  $('total-loss').textContent = money(result.total_loss);
  $('total-loss').style.fontSize = money(result.total_loss).length > 14 ? '28px' : '';
  $('budget-summary').textContent = `${result.remaining >= 0 ? 'Còn dư ' + money(result.remaining) : 'Vượt ' + money(-result.remaining)} / ngân sách ${money(result.plan.budget)}`;
  $('meter-fill').style.width = `${Math.min(100, result.total_loss / result.plan.budget * 100)}%`;
  $('lot-each').textContent = fmt(result.lot_each, 4).replace(/0+$/, '').replace(/\.$/, '');
  $('total-lot').textContent = fmt(result.total_lot, 4).replace(/0+$/, '').replace(/\.$/, '');
  $('risk-percent').textContent = result.risk_percent === null ? '—' : `${fmt(result.risk_percent)}%`;
  $('ladder-caption').textContent = `${result.entries.length} entry · ${result.plan.side.toUpperCase()}`;
  $('rows').replaceChildren(...result.entries.map(row => {
    const tr = document.createElement('tr');
    [`${String(row.index).padStart(2,'0')} / Entry`, row.entry.toFixed(context.symbol.digits), row.lot.toString(), fmt(row.loss)].forEach(value => {
      const td = document.createElement('td'); td.textContent = value; tr.append(td);
    }); return tr;
  }));
  $('sl-value').textContent = result.stop.toFixed(context.symbol.digits);
  $('price-loss').textContent = money(result.price_loss); $('fees').textContent = money(result.commission); $('reserve-value').textContent = money(result.reserve);
  $('warnings').replaceChildren(...result.warnings.map(w => { const p = document.createElement('p'); p.textContent = w; return p; }));
}
async function loadPlans() {
  const plans = await api('/api/plans');
  $('saved-list').replaceChildren();
  if (!plans.length) { const p = document.createElement('p'); p.className = 'helper'; p.textContent = 'Chưa có kế hoạch. Lưu một kế hoạch để tiếp tục trên thiết bị khác.'; $('saved-list').append(p); }
  plans.forEach(item => {
    const row = document.createElement('div'); row.className = 'saved-item';
    const description = document.createElement('div'), title = document.createElement('strong'), sub = document.createElement('small');
    title.textContent = item.name; sub.textContent = `${item.plan.symbol} · ${item.plan.side.toUpperCase()} · ${item.plan.count} entry · ${new Date(item.updated * 1000).toLocaleString('vi-VN')}`;
    description.append(title, sub);
    const actions = document.createElement('div'), open = document.createElement('button'), remove = document.createElement('button');
    open.className = 'secondary'; open.textContent = 'Mở'; remove.className = 'quiet'; remove.textContent = 'Xoá';
    open.onclick = async () => {
      try {
        invalidate();
        const sym = item.plan.symbol;
        if (!activeSymbols.includes(sym) && allSymbols.includes(sym)) {
          try {
            const added = await api('/api/symbols', {method: 'POST', body: JSON.stringify({symbol: sym})});
            activeSymbols = added.symbols;
            allSymbols = added.all_symbols;
            updateSymbolSelect(sym);
          } catch (_) {}
        }
        stopMeta = {sl_mode:item.plan.sl_mode || 'manual',swing_time:item.plan.swing_time ?? null,swing_price:item.plan.swing_price ?? null};
        swingEnabled = stopMeta.sl_mode === 'swing'; queuedSwing = false;
        $('timeframe').value = item.plan.timeframe || 'M15'; $('buffer-ticks').value = item.plan.buffer_ticks ?? 0; $('step-ticks').value = item.plan.step_ticks || 1;
        for (const [key,value] of Object.entries(item.plan)) if (form.elements.namedItem(key)) form.elements.namedItem(key).value = value;
        if (!$('symbol').value) throw new Error('Symbol của kế hoạch không có trong tài khoản hiện tại.');
        $('lot-label').hidden = item.plan.sizing !== 'fixed'; $('plan-name').value = item.name;
        await loadContext();
        $('entry').value = Number(item.plan.entry).toFixed(context.symbol.digits);
        $('stop').value = Number(item.plan.stop).toFixed(context.symbol.digits);
        if (item.currency !== context.account.currency) throw new Error(`Kế hoạch dùng ${item.currency}, tài khoản hiện tại dùng ${context.account.currency}. Hãy nhập lại ngân sách và chi phí theo đơn vị mới trước khi tính.`);
        await compute(); form.scrollIntoView({behavior:'smooth',block:'start'});
      } catch (error) { message(error.message); }
    };
    remove.onclick = async () => { try { await api(`/api/plans/${item.id}`, {method:'DELETE'}); await loadPlans(); } catch(error) { message(error.message); } };
    actions.append(open,remove); row.append(description,actions); $('saved-list').append(row);
  });
}
form.addEventListener('submit', event => compute(event, false));
form.addEventListener('input', event => {
  invalidate();
  const isFixed = form.elements.sizing.value === 'fixed';
  $('lot-label').hidden = !isFixed;
  form.elements.lot.required = isFixed;
  form.elements.budget.required = !isFixed;
  if (event.target.id === 'stop' || event.target.id === 'entry') { stopMeta.sl_mode = 'adjusted'; swingEnabled=false; queuedSwing=false; }
  stopControls();
  scheduleAutoCompute(250);
});
form.addEventListener('change', () => {
  const isFixed = form.elements.sizing.value === 'fixed';
  $('lot-label').hidden = !isFixed;
  form.elements.lot.required = isFixed;
  form.elements.budget.required = !isFixed;
  scheduleAutoCompute(0);
});
$('symbol').onchange = async () => {
  renderQuickTickers();
  const auto = swingEnabled;
  invalidate(); context = null; stopMeta = {sl_mode:'manual',swing_time:null,swing_price:null};
  $('entry').value = ''; $('stop').value = ''; stopControls();
  try {
    if (await loadContext()) {
      $('entry').value = (form.elements.side.value === 'buy' ? context.symbol.ask : context.symbol.bid).toFixed(context.symbol.digits);
      if (auto) await changeStop('swing');
    }
  } catch(error) { context=null; stopControls(); message(error.message); }
};
for (const field of [$('timeframe'),$('buffer-ticks'),...form.querySelectorAll('[name="side"]')]) field.addEventListener('change', () => {
  if (swingEnabled) changeStop('swing');
  else {
    stopMeta = {sl_mode:'manual',swing_time:null,swing_price:null};
    stopControls();
    scheduleAutoCompute(0);
  }
});
$('get-swing').onclick = () => changeStop('swing');
$('sl-up').onclick = () => changeStop('up');
$('sl-down').onclick = () => changeStop('down');
$('stop').addEventListener('keydown', event => { if (['ArrowUp','ArrowDown'].includes(event.key)) { event.preventDefault(); changeStop(event.key === 'ArrowUp' ? 'up' : 'down'); } });
for (const [id, delta] of [['minus',-1],['plus',1]]) $(id).onclick = () => {
  $('count').value = Math.max(1,Math.min(50,Number($('count').value) + delta));
  invalidate();
  scheduleAutoCompute(0);
};
$('use-quote').onclick = async () => {
  invalidate(); swingEnabled=false; queuedSwing=false;
  try {
    if (await loadContext()) {
      $('entry').value = (form.elements.side.value === 'buy' ? context.symbol.ask : context.symbol.bid).toFixed(context.symbol.digits);
      stopMeta.sl_mode = 'adjusted';
      stopControls();
      scheduleAutoCompute(0);
    }
  } catch(error) { message(error.message); }
};
$('btn-open-ticker-modal').onclick = () => openTickerModal();
$('btn-close-ticker-modal').onclick = () => closeTickerModal();
$('btn-done-ticker-modal').onclick = () => closeTickerModal();
$('ticker-modal').onclick = event => { if (event.target === $('ticker-modal')) closeTickerModal(); };
$('add-ticker-search').oninput = () => updateAddSelect();
$('add-ticker-search').onkeydown = event => {
  if (event.key === 'Enter') {
    event.preventDefault();
    if ($('add-ticker-select').value) addTicker($('add-ticker-select').value);
  }
};
$('btn-add-ticker').onclick = () => {
  if ($('add-ticker-select').value) addTicker($('add-ticker-select').value);
};
window.addEventListener('keydown', event => {
  if (event.key === 'Escape' && !$('ticker-modal').hidden) closeTickerModal();
});
$('login-form').onsubmit = async event => { event.preventDefault(); message(''); try { await api('/api/login', {method:'POST',body:JSON.stringify({password:$('password').value,remember:$('remember')?$('remember').checked:false})}); $('password').value=''; await initialize(); } catch(error) { message(error.message); } };
$('logout').onclick = async () => { await api('/api/logout',{method:'POST'}); window.location.reload(); };
$('refresh-plans').onclick = () => loadPlans().catch(error => message(error.message));
$('save').onclick = async () => {
  if (!result) return;
  $('save').disabled = true;
  try { await api('/api/plans',{method:'POST',body:JSON.stringify({name:$('plan-name').value.trim() || `${result.plan.symbol} ${result.plan.side.toUpperCase()} · ${result.plan.count} entry`, plan:result.plan,currency:context.account.currency})}); await loadPlans(); }
  catch(error) { message(error.message); } finally { $('save').disabled = !result; }
};
$('export').onclick = () => {
  if (!result) return;
  const rows = [['Entry','Price','Lot','SL',`Price loss (${context.account.currency})`,`Commission (${context.account.currency})`], ...result.entries.map(r=>[r.index,r.entry,r.lot,result.stop,r.price_loss,r.commission]), ['Reserve',result.reserve],['Total loss',result.total_loss],['Mode',context.mode]];
  const content = rows.map(row=>row.map(value=>'"'+String(value).replaceAll('"','""')+'"').join(',')).join('\r\n');
  const url = URL.createObjectURL(new Blob(['\ufeff'+content],{type:'text/csv;charset=utf-8'})); const a = document.createElement('a'); a.href=url; a.download='multi-entry-plan.csv'; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
};
initialize();
