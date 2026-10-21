const { PrefetchModel, LRUCache } = require("./model.js");
const T = require(process.argv[2]);
function run(trace, cap, policy, mode) {
  const cache = new LRUCache(cap), model = policy === "markov" ? new PrefetchModel(mode) : null;
  let hits = 0, issued = 0, useful = 0; const pending = new Map(); const preds = [];
  for (const [pid, comm, f] of trace) {
    if (pending.has(f)) { pending.delete(f); useful++; }
    hits += cache.touch(f) ? 1 : 0;
    if (model) {
      model.processAccess(pid, comm, 0, f);
      const p = model.predictNext(pid, comm, 0, 2, 0.5);
      preds.push(p.map(([x, c]) => [x, c]));
      for (const [pf] of p) if (cache.prefetch(pf)) { issued++; pending.set(pf, true); }
    }
  }
  return { hit: 100 * hits / trace.length, issued, precision: issued ? 100 * useful / issued : 0, preds };
}
const out = {};
for (const [name, trace] of Object.entries(T)) for (const cap of [100, 400]) for (const [pol, mode] of [["none", "seq"], ["markov", "seq"], ["markov", "full"]])
  out[`${name}|${cap}|${pol}|${mode}`] = run(trace, cap, pol, mode);
console.log(JSON.stringify(out));
