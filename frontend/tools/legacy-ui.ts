// @ts-nocheck

(function () {
  'use strict';
  const Core = globalThis.BufferCore;
  const $ = id => document.getElementById(id);
  const RATES = Core.RATES;
  const COLORS = ['#6d8195', '#4587ac', '#279eaa', '#7962aa', '#1864a4', '#b96c29'];
  const OBJECTIVES = { ports: '最多端口', bandwidth: '最大总带宽', high: '优先高速端口' };
  const STRATEGIES = { time: '各端口相同缓存时间', bytes: '每端口平分缓存' };
  let scene = 'a', revision = 0, activeWorker = null, workerURL = null, lastResult = null;
  const fmt = x => {
    if (x == null) return '—';
    if (!Number.isFinite(Number(x))) return '—';
    if (Number(x) !== 0 && Math.abs(Number(x)) < 0.000001) return Number(x).toExponential(3);
    return Number(x).toLocaleString('zh-CN', { maximumFractionDigits: 6 });
  };
  const intText = x => { try { return BigInt(x).toLocaleString('zh-CN'); } catch { return String(x); } };
  const name = rate => rate === 1 ? 'GE / 1G' : rate + 'GE';
  const esc = x => String(x).replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  const td = (value, field, raw) => '<td' + (field ? ' data-field="' + field + '"' : '') + (raw != null ? ' title="' + esc(raw) + '"' : '') + '>' + esc(value) + '</td>';
  const rateHTML = (rate, i) => '<span class="rate-name"><span class="rate-dot" style="--rate:' + COLORS[i] + '"></span>' + name(rate) + '</span>';

  function buildPorts() {
    $('ports-a').innerHTML = RATES.map((rate, i) => '<tr><td>' + rateHTML(rate, i) + '</td><td><input type="number" id="max-' + rate + '" aria-label="' + name(rate) + '可用上限" min="0" max="4096" step="1" value="0"></td><td><input type="checkbox" id="lock-' + rate + '" aria-label="锁定' + name(rate) + '数量"></td><td><input type="number" id="fixed-' + rate + '" aria-label="' + name(rate) + '固定数量" min="0" max="4096" step="1" value="0" disabled></td></tr>').join('');
    $('ports-b').innerHTML = RATES.map((rate, i) => '<tr><td>' + rateHTML(rate, i) + '</td><td><input type="number" id="count-' + rate + '" aria-label="' + name(rate) + '启用数量" min="0" max="4096" step="1" value="0"></td></tr>').join('');
  }
  function inputInteger(id, label) {
    const str = $(id).value.trim();
    if (!/^\d+$/.test(str) || !Number.isSafeInteger(Number(str))) throw new Error(label + '必须是非负整数。');
    return Number(str);
  }
  function readCapacity(full = false) {
    const mode = $('capacity-mode').value, defaults = Core.defaultConfig().capacity;
    return {
      mode, packetLength: $('packet-length').value.trim(),
      packetCount: full || mode === 'packets' ? $('packet-count').value.trim() : defaults.packetCount,
      fixedMB: full || mode === 'fixed' ? $('fixed-mb').value.trim() : defaults.fixedMB
    };
  }
  // Compute with the active fields only. Keep inactive edits untouched in the
  // controls; full JSON export deliberately validates the complete configuration.
  function readConfig(full = false) {
    const includeA = full || scene === 'a', includeB = full || scene === 'b';
    const defaults = Core.defaultConfig();
    const tokens = includeB ? $('compare-lengths').value.trim().split(/[\s,，;；]+/).filter(Boolean) : defaults.packetLengths.map(String);
    if (!tokens.length || tokens.some(x => !/^\d+$/.test(x) || !Number.isSafeInteger(Number(x)) || Number(x) < 1)) throw new Error('比较包长必须填写正整数，请用逗号或空格分隔。');
    const raw = {
      version: 1, scene,
      capacity: readCapacity(full),
      timeMs: includeA ? $('time-ms').value.trim() : defaults.timeMs, allocation: includeA ? $('allocation').value : defaults.allocation,
      ports: RATES.map(rate => ({ rate,
        max: includeA ? inputInteger('max-' + rate, name(rate) + '上限') : 0,
        locked: includeA && $('lock-' + rate).checked,
        fixed: includeA && (full || $('lock-' + rate).checked) ? inputInteger('fixed-' + rate, name(rate) + '固定数量') : 0,
        count: includeB ? inputInteger('count-' + rate, name(rate) + '数量') : 0
      })),
      packetLengths: [...new Set(tokens.map(Number))]
    };
    return Core.validateConfig(raw);
  }
  function setMessage(text) { $('message').textContent = text; $('message').hidden = false; }
  function clearError() { $('error').textContent = ''; $('error').hidden = true; }
  function showError(error) { $('error').textContent = error instanceof Error ? error.message : String(error); $('error').hidden = false; }
  function stopWorker() {
    if (activeWorker) activeWorker.terminate();
    activeWorker = null;
    if (workerURL) URL.revokeObjectURL(workerURL);
    workerURL = null;
    $('run').disabled = false; $('cancel').hidden = true;
  }
  function clearResults() {
    lastResult = null; $('export-csv').disabled = true;
    $('result-a').replaceChildren(); $('result-b').replaceChildren(); $('empty').hidden = false;
  }
  function invalidate(message = '参数已修改，请重新计算。') {
    revision++; stopWorker(); clearResults(); clearError(); setMessage(message);
  }
  function refreshControls() {
    const fixed = $('capacity-mode').value === 'fixed';
    $('fixed-mb-field').hidden = !fixed; $('packet-count-field').hidden = fixed;
    $('capacity-hint').textContent = fixed ? '包长影响可容纳包数，不改变总缓存。' : '改变包长时，总缓存随之变化。';
    $('allocation-hint').textContent = $('allocation').value === 'time' ? '按速率分配缓存，每个启用端口达到目标时间。' : '每口分得相同字节数，全部端口至少达到目标时间；低速端口时间更长。';
    for (const rate of RATES) $('fixed-' + rate).disabled = !$('lock-' + rate).checked;
    try {
      const c = Core.capacityInfo(readCapacity());
      $('capacity-total').textContent = fmt(c.mb);
      $('capacity-total').title = c.bytes.toString() + ' Bytes';
      $('capacity-packets').textContent = (fixed ? '当前包长可容纳约 ' : '当前模型容量 ') + intText(c.packets) + ' 个包';
    } catch {
      $('capacity-total').textContent = '—'; $('capacity-total').removeAttribute('title'); $('capacity-packets').textContent = '请填写有效的容量参数';
    }
  }
  function changeScene(next, announce = true) {
    scene = next;
    for (const key of ['a', 'b']) {
      $('tab-' + key).setAttribute('aria-selected', String(key === scene));
      $('tab-' + key).tabIndex = key === scene ? 0 : -1;
      $('panel-' + key).hidden = key !== scene;
    }
    $('run').textContent = scene === 'a' ? '计算可用端口组合' : '计算缓存时间';
    $('config-title').textContent = scene === 'a' ? '端口规划参数' : '端口使用情况';
    $('result-subtitle').textContent = scene === 'a' ? '三种目标，比较不同的端口取舍' : '全部总缓存参与分配，比较两种分配方式';
    $('empty').querySelector('h3').textContent = scene === 'a' ? '先确定端口，再比较方案' : '查看这组端口能缓存多久';
    $('empty').querySelector('p').textContent = scene === 'a' ? '输入各速率可用的端口上限，工具会分别寻找最多端口、最大总带宽和优先高速端口的组合。' : '输入实际启用的端口数量，同时查看相同缓存时间与每端口平分缓存的结果。';
    if (announce) invalidate('已切换计算场景，请填写参数后计算。');
  }
  function applyConfig(config, message, sample = false) {
    invalidate(message);
    $('capacity-mode').value = config.capacity.mode;
    $('packet-count').value = config.capacity.packetCount;
    $('packet-length').value = config.capacity.packetLength;
    $('fixed-mb').value = config.capacity.fixedMB;
    $('time-ms').value = config.timeMs; $('allocation').value = config.allocation;
    $('compare-lengths').value = config.packetLengths.join(', ');
    for (const port of config.ports) {
      $('max-' + port.rate).value = port.max; $('lock-' + port.rate).checked = port.locked;
      $('fixed-' + port.rate).value = port.fixed; $('count-' + port.rate).value = port.count;
    }
    $('sample-tag').hidden = !sample;
    changeScene(config.scene, false); refreshControls();
  }
  function table(rows) {
    return '<div class="table-wrap"><table class="data-table"><thead><tr><th>接口速率</th><th>数量 / 口</th><th>每口缓存 / MB</th><th>该类合计 / MB</th><th>缓存时间 / ms</th></tr></thead><tbody>' + rows.map((r, i) => '<tr data-rate="' + r.rate + '"' + (r.count === 0 ? ' class="inactive"' : '') + '><td>' + rateHTML(r.rate, i) + '</td>' + td(fmt(r.count), 'count') + td(fmt(r.perPortMB), 'perPortMB', r.perPortMB) + td(fmt(r.totalMB), 'totalMB', r.totalMB) + td(fmt(r.timeMs), 'timeMs', r.timeMs) + '</tr>').join('') + '</tbody></table></div>';
  }
  function metric(label, value, unit) { return '<div><span class="metric-label">' + esc(label) + '</span><span class="metric-val">' + esc(fmt(value)) + '<small>' + esc(unit) + '</small></span></div>'; }
  function allocationBar(rows, capacity, remaining) {
    const segments = rows.map((r, i) => '<span style="width:' + Math.max(0, Math.min(100, r.totalMB / capacity * 100)) + '%;background:' + COLORS[i] + '" title="' + name(r.rate) + '：' + fmt(r.totalMB) + ' MB"></span>').join('');
    const used = rows.reduce((sum, r) => sum + r.totalMB, 0);
    return '<div class="buffer-usage"><div class="bar-labels"><span>缓存分配 <strong>' + fmt(used / capacity * 100) + '%</strong></span><span>剩余 <strong class="num">' + fmt(remaining) + '</strong> MB</span></div><div class="bar" aria-label="已分配缓存占比 ' + fmt(used / capacity * 100) + '%">' + segments + '</div></div>';
  }
  function renderA(result, config) {
    $('result-a').innerHTML = result.solutions.map(s => '<article class="card solution" data-objectives="' + s.objectives.join(' ') + '"><div class="solution-title"><div class="badges">' + s.objectives.map(o => '<span class="badge">' + OBJECTIVES[o] + '</span>').join('') + '</div><span class="optimum">✓ 精确最优</span></div><div class="metrics">' + metric('启用端口', s.ports, '口') + metric('总带宽', s.bandwidth, 'Gbps') + metric('达到目标所需缓存', s.requiredMB, 'MB') + '</div>' + table(s.rows) + allocationBar(s.rows, result.capacityMB, s.remainingMB) + '<div class="panel-note">' + STRATEGIES[config.allocation] + ' · 按达到 ' + esc(config.timeMs) + ' ms 所需的最低缓存分配，未分配的容量单独保留。</div></article>').join('');
    $('result-a').insertAdjacentHTML('beforeend', '<details class="card independent"><summary>单一速率独占全部缓存时，最多可用多少口？</summary><p class="hint">各行分别假设该速率独占全部缓存，不能叠加，也不纳入其他速率的锁定数量。</p><div class="table-wrap"><table class="data-table"><thead><tr><th>速率</th><th>仅缓存约束 / 口</th><th>受可用上限约束 / 口</th></tr></thead><tbody>' + result.independent.map((r, i) => '<tr><td>' + rateHTML(r.rate, i) + '</td>' + td(intText(r.theoretical)) + td(fmt(r.limited)) + '</tr>').join('') + '</tbody></table></div></details>');
  }
  function chart(comparison, config) {
    const W = 760, H = 280, left = 76, right = 20, top = 24, bottom = 52;
    const plotW = W - left - right, plotH = H - top - bottom;
    const lengths = comparison.map(x => x.packetLength), lo = Math.min(...lengths), hi = Math.max(...lengths);
    const series = [{ label: '等时间 · 所有启用端口', color: '#14324b', dash: true, values: comparison.map(x => x.equalTimeMs) }];
    RATES.forEach((rate, i) => { if (config.ports[i].count) series.push({ label: '等容量 · ' + name(rate), color: COLORS[i], values: comparison.map(x => x.equalBytesTimes[i]) }); });
    const maxY = Math.max(...series.flatMap(s => s.values.filter(v => v != null)), 0.000001);
    const x = l => left + (hi === lo ? .5 : (l - lo) / (hi - lo)) * plotW;
    const y = value => top + plotH - value / maxY * plotH;
    const axisNum = v => v !== 0 && (Math.abs(v) >= 1e7 || Math.abs(v) < .001) ? v.toExponential(1) : fmt(Number(v.toPrecision(4)));
    let svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="不同包长下的缓存时间对比曲线"><title>横轴包长 Bytes，纵轴缓存时间 ms；详细数值见下方表格</title><rect x="' + left + '" y="' + top + '" width="' + plotW + '" height="' + plotH + '" fill="#f6f9fb"/>';
    for (let i = 0; i <= 4; i++) {
      const v = maxY * i / 4, py = y(v);
      svg += '<line x1="' + left + '" x2="' + (W - right) + '" y1="' + py + '" y2="' + py + '" stroke="#dce6ed"/><text x="' + (left - 9) + '" y="' + (py + 4) + '" text-anchor="end">' + axisNum(v) + '</text>';
    }
    const ticks = hi === lo ? [lo] : [lo, lo + (hi - lo) / 2, hi];
    for (const t of ticks) svg += '<text x="' + x(t) + '" y="' + (H - bottom + 22) + '" text-anchor="middle">' + esc(axisNum(t)) + '</text>';
    svg += '<text x="' + left + '" y="14">缓存时间 / ms</text><text x="' + (W - right) + '" y="' + (H - 6) + '" text-anchor="end">包长 / Bytes</text>';
    for (const s of series) {
      const points = s.values.map((v, i) => x(lengths[i]).toFixed(2) + ',' + y(v).toFixed(2)).join(' ');
      svg += '<polyline points="' + points + '" fill="none" stroke="' + s.color + '" stroke-width="2"' + (s.dash ? ' stroke-dasharray="7 5"' : '') + '/>';
      s.values.forEach((v, i) => { svg += '<circle cx="' + x(lengths[i]).toFixed(2) + '" cy="' + y(v).toFixed(2) + '" r="3" fill="' + s.color + '"><title>' + esc(s.label + ' · ' + lengths[i] + ' Bytes · ' + fmt(v) + ' ms') + '</title></circle>'; });
    }
    svg += '</svg>';
    return '<div class="legend">' + series.map(s => '<span style="--rate:' + s.color + '">' + s.label + (s.dash ? '（虚线）' : '') + '</span>').join('') + '</div><div class="chart" id="comparison-chart">' + svg + '</div>';
  }
  function renderB(result, config) {
    let html = result.strategies.map(s => '<article class="card solution" data-strategy="' + s.allocation + '"><div class="solution-title"><h3>' + STRATEGIES[s.allocation] + '</h3><span class="badge ' + (s.allocation === 'bytes' ? 'teal' : '') + '">全容量分配</span></div><div class="metrics">' + metric('启用端口', result.ports, '口') + metric('总带宽', result.bandwidth, 'Gbps') + metric('最短缓存时间', s.minTimeMs, 'ms') + '</div>' + table(s.rows) + '<div class="panel-note">' + (s.allocation === 'time' ? '每口缓存按速率分配；所有启用端口拥有相同的理论缓存时间。' : '总缓存按端口数平均分配；高速端口的理论缓存时间更短。') + '</div></article>').join('');
    const active = RATES.map((rate, i) => ({ rate, i })).filter(r => config.ports[r.i].count);
    html += '<section class="card comparison"><div class="comparison-head"><h3>不同包长下的缓存时间</h3><p class="hint">' + (config.capacity.mode === 'fixed' ? '固定 MB：包长只改变可容纳包数，理论时间保持不变。' : '按包数：包长越大，模型中的总缓存越大，理论时间随之变化。') + '</p></div>' + chart(result.comparison, config) + '<div class="chart-caption">横轴按实际包长比例绘制；多条曲线数值相同时会重合，以表格为准。</div><div class="table-wrap"><table class="data-table" id="comparison-table"><thead><tr><th>包长 / Bytes</th><th>总缓存 / MB</th><th>可容纳包数</th><th>等时间 / ms</th>' + active.map(r => '<th>等容量 ' + name(r.rate) + ' / ms</th>').join('') + '</tr></thead><tbody>' + result.comparison.map(c => '<tr>' + td(fmt(c.packetLength)) + td(fmt(c.capacityMB)) + td(intText(c.packets)) + td(fmt(c.equalTimeMs)) + active.map(r => td(fmt(c.equalBytesTimes[r.i]))).join('') + '</tr>').join('') + '</tbody></table></div></section>';
    $('result-b').innerHTML = html;
  }
  function acceptResult(result, config) {
    stopWorker();
    if (result.status !== 'ok') {
      clearResults();
      if (result.status === 'infeasible') showError(result.message || '固定端口组合已超过可用缓存，请调整数量、容量或目标时间。');
      setMessage(result.message || '请填写至少一个启用端口。');
      return;
    }
    $('empty').hidden = true;
    if (scene === 'a') renderA(result, config); else renderB(result, config);
    lastResult = { config, result }; $('export-csv').disabled = false;
    setMessage(scene === 'a' ? '计算完成：' + result.solutions.length + ' 组不同方案，已按三个目标完成精确求解。' : '计算完成：两种分配方式均使用全部总缓存，已比较 ' + result.comparison.length + ' 个包长。');
  }
  function run() {
    invalidate('正在检查参数…');
    let config;
    try { config = readConfig(); } catch (error) { showError(error); setMessage('请修正输入后重新计算。'); return; }
    if (scene === 'b') {
      try { acceptResult(Core.calculateTimes(config), config); } catch (error) { showError(error); setMessage('未生成计算结果，请检查参数。'); }
      return;
    }
    if (config.ports.every(p => (p.locked ? p.fixed : p.max) === 0)) { setMessage('请填写至少一个可用端口数量；锁定为 0 的端口不会启用。'); return; }
    const token = revision;
    try {
      const source = $('core-source').textContent + '\nself.onmessage=function(e){try{self.postMessage({ok:true,result:BufferCore.solve(e.data)});}catch(error){self.postMessage({ok:false,message:error.message});}};';
      workerURL = URL.createObjectURL(new Blob([source], { type: 'text/javascript' }));
      const worker = new Worker(workerURL); activeWorker = worker;
      $('run').disabled = true; $('cancel').hidden = false;
      $('message').innerHTML = '<span class="progress-dot" aria-hidden="true"></span>正在精确求解端口组合，可取消或修改参数…';
      worker.onmessage = event => {
        if (activeWorker !== worker || revision !== token) return;
        if (event.data.ok) acceptResult(event.data.result, config);
        else { stopWorker(); showError(event.data.message); setMessage('计算未完成，请检查参数。'); }
      };
      worker.onerror = event => {
        if (activeWorker !== worker) return;
        event.preventDefault(); stopWorker(); showError('组合计算未能启动或完成。请使用新版 Chrome 或 Edge 打开本文件。'); setMessage('未生成最优方案。');
      };
      worker.postMessage(config);
    } catch (error) { stopWorker(); showError('浏览器未能启动离线计算：' + error.message); setMessage('请使用新版 Chrome 或 Edge 打开本文件。'); }
  }
  function download(filename, data, type) {
    const url = URL.createObjectURL(new Blob([data], { type }));
    const link = document.createElement('a'); link.href = url; link.download = filename; document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function csvRows() {
    const { config: c, result: r } = lastResult;
    const rows = [['端口缓存计算器', 'v1.0'], ['计算口径', '十进制MB；连续字节；单方向满速；理论时间'], ['场景', c.scene === 'a' ? '给定时间求端口' : '给定端口求时间'], ['容量模式', c.capacity.mode === 'packets' ? '包数×包长' : '固定MB'], ['总包数参数', c.capacity.mode === 'packets' ? c.capacity.packetCount : '不适用'], ['当前包长(Bytes)', c.capacity.packetLength], ['固定容量参数(MB)', c.capacity.mode === 'fixed' ? c.capacity.fixedMB : '不适用'], ['本次总缓存(MB)', r.capacityMB], ['目标缓存时间(ms)', c.scene === 'a' ? c.timeMs : '不适用'], ['分配方式', c.scene === 'a' ? STRATEGIES[c.allocation] : '相同时间与每口平分两种'], ['比较包长(Bytes)', c.scene === 'b' ? c.packetLengths.join(' / ') : '不适用'], [], ['输入端口', '速率(Gbps)', '场景A可用上限', '场景A锁定', '场景A固定数量', '场景B启用数量']];
    c.ports.forEach(p => rows.push(['输入', p.rate, c.scene === 'a' ? p.max : '', c.scene === 'a' ? (p.locked ? '是' : '否') : '', c.scene === 'a' && p.locked ? p.fixed : '', c.scene === 'b' ? p.count : '']));
    rows.push([]);
    const addRows = (label, details) => details.forEach(p => rows.push([label, p.rate, p.count, p.perPortMB, p.totalMB, p.timeMs == null ? '' : p.timeMs]));
    if (c.scene === 'a') {
      rows.push(['方案目标', '总口数', '总带宽(Gbps)', '所需缓存(MB)', '剩余缓存(MB)']);
      r.solutions.forEach(s => rows.push([s.objectives.map(o => OBJECTIVES[o]).join(' / '), s.ports, s.bandwidth, s.requiredMB, s.remainingMB]));
      rows.push([], ['方案目标', '速率(Gbps)', '数量', '每口缓存(MB)', '该类总缓存(MB)', '缓存时间(ms)']);
      r.solutions.forEach(s => addRows(s.objectives.map(o => OBJECTIVES[o]).join(' / '), s.rows));
      rows.push([], ['独占参考，不可叠加', '速率(Gbps)', '仅缓存约束最大口数', '受可用上限约束口数']);
      r.independent.forEach(p => rows.push(['独占参考', p.rate, p.theoretical, p.limited]));
    } else {
      rows.push(['分配方式', '速率(Gbps)', '数量', '每口缓存(MB)', '该类总缓存(MB)', '缓存时间(ms)']);
      r.strategies.forEach(s => addRows(STRATEGIES[s.allocation], s.rows));
      rows.push([], ['比较包长(Bytes)', '总缓存(MB)', '可容纳包数', '等时间(ms)', ...RATES.map(rate => '等容量' + rate + 'G(ms)')]);
      r.comparison.forEach(p => rows.push([p.packetLength, p.capacityMB, p.packets, p.equalTimeMs, ...p.equalBytesTimes.map(x => x == null ? '' : x)]));
    }
    return rows;
  }
  function toCSV(rows) { return '\ufeff' + rows.map(row => row.map(v => '"' + String(v).replace(/"/g, '""') + '"').join(',')).join('\r\n'); }
  async function importConfig() {
    const file = $('import-json').files[0]; if (!file) return;
    try {
      if (file.size > 1024 * 1024) throw new Error('配置文件不能超过 1 MB。');
      const config = Core.validateConfig(JSON.parse((await file.text()).replace(/^\uFEFF/, '')));
      applyConfig(config, '配置已导入，请点击计算生成结果。');
    } catch (error) { showError('导入失败，原配置保持不变：' + error.message); }
    finally { $('import-json').value = ''; }
  }

  buildPorts();
  const configElements = ['capacity-mode', 'packet-count', 'packet-length', 'fixed-mb', 'time-ms', 'allocation', 'compare-lengths', ...RATES.flatMap(rate => ['max-' + rate, 'lock-' + rate, 'fixed-' + rate, 'count-' + rate])];
  configElements.forEach(id => $(id).addEventListener('input', () => { $('sample-tag').hidden = true; invalidate(); refreshControls(); }));
  $('tab-a').addEventListener('click', () => { if (scene !== 'a') changeScene('a'); });
  $('tab-b').addEventListener('click', () => { if (scene !== 'b') changeScene('b'); });
  document.querySelector('[role="tablist"]').addEventListener('keydown', event => { if (['ArrowLeft', 'ArrowRight'].includes(event.key)) { event.preventDefault(); changeScene(scene === 'a' ? 'b' : 'a'); $('tab-' + scene).focus(); } });
  $('run').addEventListener('click', run);
  $('cancel').addEventListener('click', () => invalidate('计算已取消，未生成最优方案。'));
  $('reset').addEventListener('click', () => applyConfig(Core.defaultConfig(), '已恢复默认参数，请填写端口数量。'));
  $('demo').addEventListener('click', () => {
    const c = Core.defaultConfig(); c.scene = scene; c.capacity.mode = 'fixed'; c.capacity.fixedMB = '25';
    c.ports.find(p => p.rate === 100).max = 2; c.ports.find(p => p.rate === 25).max = 8;
    c.ports.find(p => p.rate === 100).count = 1; c.ports.find(p => p.rate === 25).count = 1;
    applyConfig(c, '已载入演示：25 MB；规划上限 2×100GE + 8×25GE，实际使用 1×100GE + 1×25GE。', true);
  });
  $('export-json').addEventListener('click', () => {
    clearError();
    try { download('端口缓存配置.json', JSON.stringify(readConfig(true), null, 2), 'application/json;charset=utf-8'); setMessage('配置已导出。下次打开工具可导入恢复。'); }
    catch (error) { showError('导出配置需完整有效的两场景参数（包括未启用的字段）：' + error.message); }
  });
  $('import-button').addEventListener('click', () => $('import-json').click());
  $('import-json').addEventListener('change', importConfig);
  $('export-csv').addEventListener('click', () => { if (lastResult) download('端口缓存结果-' + (lastResult.config.scene === 'a' ? '端口组合' : '缓存时间') + '.csv', toCSV(csvRows()), 'text/csv;charset=utf-8'); });
  applyConfig(Core.defaultConfig(), '填写端口数量，或载入演示参数后计算。');
})();
