/* Port of prefetch_model.PrefetchModel (variable-order Markov + context) and simulate.LRUCache. */
(function (root) {
  const SEP = "\u0001";
  function ext(f) { const b = f.slice(f.lastIndexOf("/") + 1); const i = b.lastIndexOf("."); return i > 0 ? b.slice(i) : ".none"; }
  class PrefetchModel {
    constructor(mode = "full", maxWindow = 5, wSeq = 0.6) {
      this.mode = mode; this.W = maxWindow; this.wSeq = wSeq;
      this.history = new Map(); this.freq = new Map(); this.totals = new Map();
      this.fileTotal = new Map(); this.fileComm = new Map(); this.fileTime = new Map();
      this.commTotal = new Map(); this.commExt = new Map();
    }
    _hist(pid) { let h = this.history.get(pid); if (!h) { h = []; this.history.set(pid, h); } return h; }
    processAccess(pid, comm, bucket, f) {
      const h = this._hist(pid);
      if (this.mode !== "static") {
        for (let i = 0; i < h.length; i++) {
          const ctx = h.slice(i).join(SEP);
          let row = this.freq.get(ctx);
          if (!row) { row = new Map(); this.freq.set(ctx, row); this.totals.set(ctx, 0); }
          row.set(f, (row.get(f) || 0) + 1); this.totals.set(ctx, this.totals.get(ctx) + 1);
        }
        this.fileTotal.set(f, (this.fileTotal.get(f) || 0) + 1);
        const inc = (m, k, sub) => { let d = m.get(k); if (!d) { d = new Map(); m.set(k, d); } d.set(sub, (d.get(sub) || 0) + 1); };
        inc(this.fileComm, f, comm); inc(this.fileTime, f, bucket);
        this.commTotal.set(comm, (this.commTotal.get(comm) || 0) + 1); inc(this.commExt, comm, ext(f));
      }
      h.push(f); if (h.length > this.W) h.shift();
    }
    _pCtx(f, comm, bucket) {
      const ft = this.fileTotal.get(f) || 0; if (!ft) return 0;
      const pComm = (this.fileComm.get(f).get(comm) || 0) / ft;
      const pTime = (this.fileTime.get(f).get(bucket) || 0) / ft;
      const ct = this.commTotal.get(comm) || 0;
      const pExt = ct ? ((this.commExt.get(comm) || new Map()).get(ext(f)) || 0) / ct : 0;
      return (pComm + pTime + pExt) / 3;
    }
    predictNext(pid, comm, bucket, topK = null, minConf = 0) {
      const h = this.history.get(pid); if (!h || !h.length) return [];
      const scores = new Map(); let tw = 0;
      for (let i = 0; i < h.length; i++) {
        const ctx = h.slice(i).join(SEP), row = this.freq.get(ctx); if (!row) continue;
        const L = h.length - i, denom = this.totals.get(ctx); tw += L;
        for (const [nxt, cnt] of row) scores.set(nxt, (scores.get(nxt) || 0) + L * cnt / denom);
      }
      if (!scores.size) return [];
      const ranked = [];
      for (const [f, s] of scores) {
        const pSeq = s / tw;
        const conf = this.mode === "full" ? this.wSeq * pSeq + (1 - this.wSeq) * this._pCtx(f, comm, bucket) : pSeq;
        if (conf >= minConf) ranked.push([f, conf]);
      }
      ranked.sort((a, b) => b[1] - a[1]);          // stable, like Python's sort
      return topK ? ranked.slice(0, topK) : ranked;
    }
    get size() { return this.freq.size; }
  }
  class LRUCache {                                  // insertion-ordered Map = OrderedDict
    constructor(cap) { this.cap = cap; this.d = new Map(); this.evicted = []; }
    touch(f) { const hit = this.d.has(f); this.d.delete(f); this.d.set(f, true); this._evict(); return hit; }
    prefetch(f) { if (this.d.has(f)) return false; this.d.set(f, true); this._evict(); return true; }
    _evict() { while (this.d.size > this.cap) { const k = this.d.keys().next().value; this.d.delete(k); this.evicted.push(k); } }
  }
  const api = { PrefetchModel, LRUCache };
  if (typeof module !== "undefined") module.exports = api; else root.PF = api;
})(this);
