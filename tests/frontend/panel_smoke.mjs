// Panel smoke test: renders every tab against a mocked hass.callWS in jsdom.
// Run: (cd tests/frontend && npm i jsdom@24 --no-save && node panel_smoke.mjs)   (jsdom >24 needs a newer Node)
import { JSDOM } from "jsdom";
import path from "node:path";
const root = new URL("../../custom_components/assist_learner/frontend/", import.meta.url).href;
const dom = new JSDOM("<!doctype html><body></body>", { pretendToBeVisual: true, url: "http://localhost/" });
for (const k of Object.getOwnPropertyNames(dom.window)) { if (!(k in globalThis)) { try { Object.defineProperty(globalThis, k, { get: () => dom.window[k], configurable: true }); } catch {} } }


const calls = [];
const params = {};
const data = {
  overview: { pending_proposals: 1, pending_commands: 1, learned: 1, commands: 1, fallbacks_today: 3, local_hits_today: 2, ai_task: "ai_task.x", language: "en", send_states: true },
  "proposals/list": [{ id: "p1", type: "new", template: "put on {track} from {artist}", count: 2, confidence: 0.95, examples: ["put on x from y"], warnings: ["careful"], diff: null }],
  "sentences/list": [
    { source: "learned", id: "lt_1", title: "put on {track} from {artist}", sentences: ["put on {track} from {artist}"], enabled: true, hits: 4, last_used: null, editable: true },
    { source: "custom", id: "cmd_1", title: "Outside", status: "pending", type: "answer", sentences: ["a b", "c d"], editable: true, enabled: false },
    { source: "custom", id: "cmd_1", title: "Outside", status: "active", type: "answer", sentences: ["a b", "c d"], editable: true, enabled: true },
    { source: "external", kind: "file", id: "f#I", title: "I", sentences: ["x y"], file: "/c/x.yaml", editable: false },
  ],
  "commands/get": { id: "cmd_1", name: "Outside", type: "answer", status: "pending", description: "d", sentences: ["a b", "c d"], response_template: "It is {{ states('sensor.t') }}", slots: {}, entities: { t: "sensor.t" }, validation: { ok: true, preview: "It is 7", errors: [], warnings: [], flags: [], calls: [] }, notes: "n" },
  "commands/to_yaml": { yaml: "name: Outside\nsentences:\n- a b\n" },
  "commands/from_yaml": { fields: { name: "Edited" } },
  "misses/list": [{ id: "m", timestamp: "2026-01-01T00:00:00Z", text: "hello", success: true, processed: false }],
  stats: { daily: [{ date: "2026-01-01", fallbacks: 2, local_hits: 1 }], learned: [{ id: "lt_1", template: "t", hits: 1 }], commands: [{ id: "c", name: "n", hits: 2 }] },
  "executions/list": [{ time: "2026-01-01T00:00:00Z", command: "n", calls: [{ service: "light.turn_on" }], ok: true }],
  "sentences/test": { matched: true, intent: "X", slots: { a: "b" }, source: "learned", title: "T" },
  "import/preview": [{ intent: "I", file: "/c/x.yaml", importable: false, unmappable: "wildcard", validation: null }],
};
const hass = { states: { "sensor.t": {} }, callWS: async (m) => { calls.push(m.type); params[m.type.replace('assist_learner/', '')] = m; const k = m.type.replace("assist_learner/", ""); if (!(k in data)) return {}; return data[k]; } };
await import(root + "panel.js");
const el = document.createElement("assist-learner-panel");
el.hass = hass; document.body.appendChild(el);
const tick = () => new Promise((r) => setTimeout(r, 30));
await tick(); await el.updateComplete;
const text = () => el.shadowRoot.textContent.replace(/\s+/g, " ");
const expect = (cond, msg) => { if (!cond) { console.error("FAIL:", msg, "\n", text().slice(0, 600)); process.exit(1); } };
expect(text().includes("Learned from LLM fallbacks") && text().includes("put on {track} from {artist}"), "pending tab proposals");
expect(text().includes("It is 7"), "command preview rendered");
for (const tab of ["sentences", "new", "misses", "stats"]) { el._setTab(tab); await tick(); await el.updateComplete; }
expect(text().includes("fallbacks"), "stats rendered");
el._setTab("sentences"); await tick(); await el.updateComplete;
el._open = { "learned_lt_1": true }; await el.updateComplete;
expect(text().includes("Disable"), "editor buttons");
el._testText = "hello"; await el._test(); await el.updateComplete;
expect(text().includes("Matches"), "test box result");
el._imports = data["import/preview"]; await el.updateComplete;
expect(text().includes("stays external"), "import preview");
el._setTab("new"); await tick(); await el.updateComplete;
expect(text().includes("exact payload") === false, "no payload before preview");
// builder / YAML editor for a custom command
el._setTab("sentences"); await tick(); await el.updateComplete;
el._open = { ...el._open, custom_cmd_1: true };
await el._startEdit({ source: "custom", id: "cmd_1" }, "ed_custom_cmd_1"); await tick(); await el.updateComplete;
expect(text().includes("Sentences") && text().includes("+ Add sentence") && text().includes("+ Add action"), "builder rendered");
expect(el.shadowRoot.querySelector('input[placeholder="label"]')?.value === "t", "entity row populated from command");
await el._switchMode("ed_custom_cmd_1", el._editing["ed_custom_cmd_1"], "yaml"); await el.updateComplete;
expect(el.shadowRoot.querySelector("textarea.yaml")?.value.includes("name: Outside"), "yaml shown");
expect(params["commands/to_yaml"].fields.sentences.length === 2, "builder fields sent to yaml converter");
await el._saveCmd({ id: "cmd_1" }, "ed_custom_cmd_1", el._editing["ed_custom_cmd_1"]); await el.updateComplete;
expect(params["commands/update"].fields.name === "Edited", "yaml save sends parsed fields");
expect(!el._editing["ed_custom_cmd_1"], "editor closed after save");
console.log("SMOKE_OK", calls.length, "ws calls");
process.exit(0);
