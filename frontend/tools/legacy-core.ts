// @ts-nocheck

(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.BufferCore = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const RATES = Object.freeze([1, 10, 25, 50, 100, 400]);
  const MAX_SAFE = 9007199254740991n;
  const SCALE = 1000000n;
  const BYTES_PER_GBPS_MS = 125000n;

  function defaultConfig() {
    return {
      version: 1, scene: 'a',
      capacity: { mode: 'packets', packetCount: '1800000', packetLength: '3072', fixedMB: '5529.6' },
      timeMs: '1', allocation: 'time',
      ports: RATES.map(rate => ({ rate, max: 0, locked: false, fixed: 0, count: 0 })),
      packetLengths: [64, 128, 256, 512, 1024, 1500, 3072, 9000]
    };
  }

  function fail(message) { throw new Error(message); }
  function shape(value, keys, name) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) fail(name + '必须是完整对象。');
    const actual = Object.keys(value);
    if (actual.length !== keys.length || actual.some(key => !keys.includes(key)) || keys.some(key => !Object.prototype.hasOwnProperty.call(value, key))) fail(name + '包含缺失或未知字段。');
  }
  function choice(value, allowed, name) {
    if (!allowed.includes(value)) fail(name + '无效。');
    return value;
  }
  function positiveIntegerString(value, name) {
    if (typeof value !== 'string' || value.length > 32 || !/^\d+$/.test(value)) fail(name + '必须是正整数字符串。');
    const n = BigInt(value);
    if (n < 1n || n > MAX_SAFE) fail(name + '必须在 1 至 9007199254740991 之间。');
    return value;
  }
  // Six fractional decimal places map MB to whole bytes and ms to micro-ms.
  // Parsing the source string avoids rounding across a feasibility boundary.
  function decimalScaled(value, name) {
    if (typeof value !== 'string' || value.length > 40 || !/^\d+(?:\.\d{1,6})?$/.test(value)) fail(name + '必须是正数，且最多有 6 位小数。');
    const parts = value.split('.'), whole = BigInt(parts[0]);
    if (whole > MAX_SAFE) fail(name + '的整数部分不能超过 9007199254740991。');
    const n = whole * SCALE + BigInt((parts[1] || '').padEnd(6, '0'));
    if (n <= 0n) fail(name + '必须大于 0。');
    return n;
  }
  function portInteger(value, name) {
    if (!Number.isInteger(value) || value < 0 || value > 4096) fail(name + '必须是 0 至 4096 的整数。');
    return value;
  }
  function validateCapacity(raw) {
    shape(raw, ['mode', 'packetCount', 'packetLength', 'fixedMB'], '容量配置');
    const mode = choice(raw.mode, ['packets', 'fixed'], '容量模式');
    const packetCount = positiveIntegerString(raw.packetCount, '包数');
    const packetLength = positiveIntegerString(raw.packetLength, '包长');
    decimalScaled(raw.fixedMB, '固定容量 MB');
    return { mode, packetCount, packetLength, fixedMB: raw.fixedMB };
  }
  function validateConfig(raw) {
    shape(raw, ['version', 'scene', 'capacity', 'timeMs', 'allocation', 'ports', 'packetLengths'], '配置');
    if (raw.version !== 1) fail('仅支持版本 1 的配置。');
    const scene = choice(raw.scene, ['a', 'b'], '计算场景');
    const capacity = validateCapacity(raw.capacity);
    decimalScaled(raw.timeMs, '目标时间 ms');
    const allocation = choice(raw.allocation, ['time', 'bytes'], '分配方式');
    if (!Array.isArray(raw.ports) || raw.ports.length !== RATES.length) fail('端口配置必须包含六种速率。');
    const ports = raw.ports.map((p, i) => {
      shape(p, ['rate', 'max', 'locked', 'fixed', 'count'], RATES[i] + 'G 端口配置');
      if (p.rate !== RATES[i]) fail('端口速率或排列顺序无效。');
      const max = portInteger(p.max, RATES[i] + 'G 物理上限');
      const fixed = portInteger(p.fixed, RATES[i] + 'G 固定数量');
      const count = portInteger(p.count, RATES[i] + 'G 实际数量');
      if (typeof p.locked !== 'boolean') fail('固定开关必须为布尔值。');
      if (p.locked && fixed > max) fail(RATES[i] + 'G 固定数量不能超过物理上限。');
      return { rate: RATES[i], max, locked: p.locked, fixed, count };
    });
    if (!Array.isArray(raw.packetLengths) || raw.packetLengths.length < 1 || raw.packetLengths.length > 64) fail('对比包长必须包含 1 至 64 个值。');
    const packetLengths = raw.packetLengths.map(length => {
      if (!Number.isSafeInteger(length) || length <= 0) fail('对比包长必须为正安全整数。');
      return length;
    });
    return { version: 1, scene, capacity, timeMs: raw.timeMs, allocation, ports, packetLengths };
  }
  function capacityUnchecked(capacity, overrideLength) {
    const length = overrideLength === undefined ? BigInt(capacity.packetLength) : BigInt(overrideLength);
    const bytes = capacity.mode === 'packets' ? BigInt(capacity.packetCount) * length : decimalScaled(capacity.fixedMB, '固定容量 MB');
    return { bytes, mb: Number(bytes) / 1000000, packets: (capacity.mode === 'packets' ? BigInt(capacity.packetCount) : bytes / length).toString() };
  }
  function capacityInfo(capacity) { return capacityUnchecked(validateCapacity(capacity)); }

  function totals(counts) {
    let ports = 0, bandwidth = 0, maxRate = 0;
    for (let i = 0; i < RATES.length; i++) {
      ports += counts[i]; bandwidth += counts[i] * RATES[i];
      if (counts[i]) maxRate = RATES[i];
    }
    return { ports, bandwidth, maxRate };
  }
  function compare(a, b, objective) {
    if (!b) return 1;
    const aa = totals(a), bb = totals(b);
    if (objective === 'ports') {
      if (aa.ports !== bb.ports) return aa.ports - bb.ports;
      if (aa.bandwidth !== bb.bandwidth) return aa.bandwidth - bb.bandwidth;
    } else if (objective === 'bandwidth') {
      if (aa.bandwidth !== bb.bandwidth) return aa.bandwidth - bb.bandwidth;
      if (aa.ports !== bb.ports) return aa.ports - bb.ports;
    }
    for (let i = RATES.length - 1; i >= 0; i--) if (a[i] !== b[i]) return a[i] - b[i];
    return 0;
  }

  // Exact bounded knapsack indexed by integer aggregate Gbps. At each exact
  // bandwidth retain the largest port count. Rates are added low to high;
  // equal-value queue entries retain the oldest predecessor, which selects
  // more of the current (highest) rate and preserves the specified lexicographic tie.
  // The monotone queue reduces each rate layer to O(budget), rather than O(max*budget).
  function optimizeTime(base, limits, budgetBig) {
    const fixedBandwidth = totals(base).bandwidth;
    const maxExtra = limits.reduce((sum, max, i) => sum + max * RATES[i], 0);
    const remainingBig = budgetBig - BigInt(fixedBandwidth);
    const budget = remainingBig >= BigInt(maxExtra) ? maxExtra : Number(remainingBig);
    const high = base.slice();
    let left = budget;
    for (let i = RATES.length - 1; i >= 0; i--) {
      const count = Math.min(limits[i], Math.floor(left / RATES[i]));
      high[i] += count; left -= count * RATES[i];
    }
    if (budget === maxExtra) {
      const all = base.map((n, i) => n + limits[i]);
      return { ports: all, bandwidth: all, high: all };
    }
    let previous = new Int16Array(1), previousLimit = 0;
    const layers = [];
    for (let i = 0; i < RATES.length; i++) {
      const rate = RATES[i], max = limits[i];
      if (!max || rate > budget) continue;
      const limit = Math.min(budget, previousLimit + rate * max);
      const next = new Int16Array(limit + 1); next.fill(-1);
      const selections = new Uint16Array(limit + 1);
      const queue = new Int32Array(Math.floor(limit / rate) + 1);
      for (let residue = 0; residue < rate && residue <= limit; residue++) {
        let head = 0, tail = 0;
        const last = Math.floor((limit - residue) / rate);
        for (let j = 0; j <= last; j++) {
          const at = residue + j * rate;
          while (head < tail && queue[head] < j - max) head++;
          if (at <= previousLimit && previous[at] >= 0) {
            const score = previous[at] - j;
            while (head < tail) {
              const k = queue[tail - 1];
              if (previous[residue + k * rate] - k >= score) break;
              tail--;
            }
            queue[tail++] = j;
          }
          if (head < tail) {
            const k = queue[head];
            next[at] = previous[residue + k * rate] + j - k;
            selections[at] = j - k;
          }
        }
      }
      layers.push({ index: i, rate, selections });
      previous = next; previousLimit = limit;
    }
    let bestPortsAt = 0, bestBandwidthAt = 0;
    for (let s = 0; s <= previousLimit; s++) {
      if (previous[s] < 0) continue;
      bestBandwidthAt = s;
      if (previous[s] >= previous[bestPortsAt]) bestPortsAt = s;
    }
    function recover(at) {
      const counts = base.slice();
      for (let layer = layers.length - 1; layer >= 0; layer--) {
        const { index, rate, selections } = layers[layer], count = selections[at];
        counts[index] += count; at -= count * rate;
      }
      return counts;
    }
    return { ports: recover(bestPortsAt), bandwidth: recover(bestBandwidthAt), high };
  }

  // For each possible actual maximum rate R, equal bytes reduces to a total
  // port-count budget. Fixed counts are placed first and one R port is reserved.
  // Filling remaining slots high to low is optimal for all three objectives
  // within that R; compare the six resulting candidates using the public ties.
  function optimizeBytes(base, limits, budgetBig) {
    const fixed = totals(base), candidates = [];
    if (!fixed.ports) candidates.push(base.slice());
    for (let highest = 0; highest < RATES.length; highest++) {
      const rate = RATES[highest];
      if (fixed.maxRate > rate || (!base[highest] && !limits[highest])) continue;
      const counts = base.slice();
      if (!counts[highest]) counts[highest] = 1;
      let used = counts.reduce((sum, n) => sum + n, 0);
      const capacity = budgetBig / BigInt(rate);
      if (BigInt(used) > capacity) continue;
      const maximum = base.reduce((sum, n) => sum + n, 0) + limits.reduce((sum, n, i) => sum + (i <= highest ? n : 0), 0);
      const portBudget = capacity > BigInt(maximum) ? maximum : Number(capacity);
      for (let i = highest; i >= 0; i--) {
        const available = limits[i] - (counts[i] - base[i]);
        const add = Math.min(available, portBudget - used);
        counts[i] += add; used += add;
      }
      candidates.push(counts);
    }
    const best = {};
    for (const objective of ['ports', 'bandwidth', 'high']) {
      for (const candidate of candidates) if (compare(candidate, best[objective], objective) > 0) best[objective] = candidate;
    }
    return best;
  }

  function solution(counts, allocation, timeScaled, capacity) {
    const metrics = totals(counts), timeMs = Number(timeScaled) / 1000000;
    const weight = allocation === 'time' ? metrics.bandwidth : metrics.ports * metrics.maxRate;
    const requiredScaledBytes = BYTES_PER_GBPS_MS * timeScaled * BigInt(weight);
    const rows = RATES.map((rate, i) => {
      const count = counts[i], effectiveRate = allocation === 'time' ? rate : metrics.maxRate;
      const perScaled = count ? BYTES_PER_GBPS_MS * timeScaled * BigInt(effectiveRate) : 0n;
      return { rate, count, perPortMB: Number(perScaled) / 1e12, totalMB: Number(perScaled * BigInt(count)) / 1e12, timeMs: count ? (allocation === 'time' ? timeMs : timeMs * metrics.maxRate / rate) : null };
    });
    return {
      objectives: [], counts: counts.slice(), ...metrics,
      requiredMB: Number(requiredScaledBytes) / 1e12,
      remainingMB: Number(capacity.bytes * SCALE - requiredScaledBytes) / 1e12,
      rows
    };
  }

  function solve(raw) {
    const config = validateConfig(raw), capacity = capacityUnchecked(config.capacity);
    const timeScaled = decimalScaled(config.timeMs, '目标时间 ms');
    const budgetBig = capacity.bytes * SCALE / (BYTES_PER_GBPS_MS * timeScaled);
    const independent = config.ports.map(p => {
      const theoretical = budgetBig / BigInt(p.rate);
      return { rate: p.rate, theoretical: theoretical.toString(), limited: theoretical >= BigInt(p.max) ? p.max : Number(theoretical) };
    });
    const result = { status: 'ok', message: '', capacityMB: capacity.mb, solutions: [], independent };
    const base = config.ports.map(p => p.locked ? p.fixed : 0);
    const limits = config.ports.map(p => p.locked ? 0 : p.max);
    const fixed = totals(base), fixedWeight = config.allocation === 'time' ? fixed.bandwidth : fixed.ports * fixed.maxRate;
    if (BigInt(fixedWeight) > budgetBig) return { ...result, status: 'infeasible', message: '固定端口在目标时间下的最低缓存需求已超过总容量。' };
    if (!base.some(Boolean) && !limits.some(Boolean)) return { ...result, status: 'empty', message: '当前没有可启用的端口，请设置物理上限或调整固定数量。' };
    const best = config.allocation === 'time' ? optimizeTime(base, limits, budgetBig) : optimizeBytes(base, limits, budgetBig);
    for (const objective of ['ports', 'bandwidth', 'high']) {
      const counts = best[objective];
      let existing = result.solutions.find(s => s.counts.every((n, i) => n === counts[i]));
      if (!existing) { existing = solution(counts, config.allocation, timeScaled, capacity); result.solutions.push(existing); }
      existing.objectives.push(objective);
    }
    result.message = result.solutions.some(s => s.ports) ? '已精确计算三种目标的最优组合；相同组合已合并。' : '当前容量无法支撑任一可用端口达到目标时间，最优组合为 0 口。';
    return result;
  }

  function fullAllocation(counts, capacity, allocation) {
    const { ports, bandwidth, maxRate } = totals(counts);
    const equalTimeMs = bandwidth ? 8 * capacity.mb / bandwidth : null;
    const rows = RATES.map((rate, i) => {
      const count = counts[i];
      const perPortMB = !count ? 0 : allocation === 'time' ? capacity.mb * rate / bandwidth : capacity.mb / ports;
      return { rate, count, perPortMB, totalMB: perPortMB * count, timeMs: !count ? null : allocation === 'time' ? equalTimeMs : 8 * capacity.mb / (ports * rate) };
    });
    return { allocation, minTimeMs: !ports ? null : allocation === 'time' ? equalTimeMs : 8 * capacity.mb / (ports * maxRate), rows };
  }
  function calculateTimes(raw) {
    const config = validateConfig(raw), capacity = capacityUnchecked(config.capacity);
    const counts = config.ports.map(p => p.count), { ports, bandwidth } = totals(counts);
    const strategies = ['time', 'bytes'].map(allocation => fullAllocation(counts, capacity, allocation));
    const comparison = config.packetLengths.map(packetLength => {
      const cap = capacityUnchecked(config.capacity, packetLength);
      return {
        packetLength, capacityMB: cap.mb, packets: cap.packets,
        equalTimeMs: bandwidth ? 8 * cap.mb / bandwidth : null,
        equalBytesTimes: RATES.map((rate, i) => counts[i] ? 8 * cap.mb / (ports * rate) : null)
      };
    });
    return { status: ports ? 'ok' : 'empty', capacityMB: capacity.mb, packets: capacity.packets, ports, bandwidth, strategies, comparison };
  }

  return Object.freeze({ RATES, defaultConfig, validateConfig, capacityInfo, solve, calculateTimes });
});
