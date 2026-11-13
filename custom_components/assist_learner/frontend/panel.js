// Assist Learner panel. Plain ES module + vendored Lit (no build step, no CDN).
// All Home Assistant specifics (websocket calls, entity picker) live in the small helpers at the top so a
// frontend change only needs fixing here (plan risk 12).
import { LitElement, html, css, nothing } from "./vendor/lit.js";

const WS = "assist_learner/";

const callWS = (hass, type, params = {}) => hass.callWS({ type: WS + type, ...params });
const fmt = (iso) => (iso ? new Date(iso).toLocaleString() : "never");
const lines = (text) => text.split("\n").map((s) => s.trim()).filter(Boolean);

// ---- command <-> builder form conversion (the API form is what is stored / shown as YAML) ----------
const showVal = (v) => (typeof v === "string" ? v : JSON.stringify(v));
const parseVal = (v) => {
  const t = String(v).trim();
  try {
    return JSON.parse(t);
  } catch (_) {
    return t;
  }
};
const toForm = (f) => ({
  name: f.name || "",
  type: f.type || "answer",
  sentences: [...(f.sentences || [])],
  slots: Object.entries(f.slots || {}).map(([name, d]) => ({
    name, type: d.type || "number", min: d.min ?? 0, max: d.max ?? 100, step: d.step ?? 1, values: (d.values || []).join(", "),
  })),
  entities: Object.entries(f.entities || {}).map(([role, entity]) => ({ role, entity })),
  actions: (f.actions || []).map((a) => ({
    service: a.service || "", targets: [...(a.targets || [])],
    data: Object.entries(a.data || {}).map(([k, v]) => ({ k, v: showVal(v) })),
  })),
  response_template: f.response_template || "",
  unavailable_template: f.unavailable_template || "",
});
const fromForm = (d) => ({
  name: d.name,
  type: d.type,
  sentences: d.sentences.map((x) => x.trim()).filter(Boolean),
  slots: Object.fromEntries(d.slots.filter((x) => x.name).map((x) => [x.name, x.type === "number"
    ? { type: "number", min: Number(x.min), max: Number(x.max), step: Number(x.step) || 1 }
    : { type: "choice", values: String(x.values).split(",").map((v) => v.trim()).filter(Boolean) }])),
  entities: Object.fromEntries(d.entities.filter((e) => e.role && e.entity).map((e) => [e.role, e.entity])),
  actions: d.actions.filter((a) => a.service).map((a) => ({
    service: a.service, targets: a.targets,
    data: Object.fromEntries(a.data.filter((x) => x.k).map((x) => [x.k, parseVal(x.v)])),
  })),
  response_template: d.response_template,
  unavailable_template: d.unavailable_template,
});

/** Entity picker: HA's own when available, otherwise a searchable input with a datalist. */
class AlEntityPicker extends LitElement {
  static properties = { hass: {}, value: { type: String } };
  render() {
    if (customElements.get("ha-entity-picker")) {
      return html`<ha-entity-picker
        .hass=${this.hass}
        .value=${this.value}
        allow-custom-entity
        @value-changed=${(e) => this._emit(e.detail.value)}
      ></ha-entity-picker>`;
    }
    const ids = Object.keys(this.hass?.states || {}).sort();
    const list = `dl-${Math.random().toString(36).slice(2)}`;
    return html`<input
        list=${list}
        .value=${this.value || ""}
        placeholder="sensor.outdoor_temperature"
        @change=${(e) => this._emit(e.target.value.trim())}
      /><datalist id=${list}>${ids.map((i) => html`<option value=${i}></option>`)}</datalist>`;
  }
  _emit(value) {
    this.dispatchEvent(new CustomEvent("picked", { detail: { value } }));
  }
  static styles = css`input { width: 100%; box-sizing: border-box; padding: 8px; }`;
}
customElements.define("al-entity-picker", AlEntityPicker);

class AssistLearnerPanel extends LitElement {
  static properties = {
    hass: {},
    narrow: { type: Boolean },
    panel: {},
    _tab: { state: true },
    _overview: { state: true },
    _proposals: { state: true },
    _sentences: { state: true },
    _filter: { state: true },
    _misses: { state: true },
    _stats: { state: true },
    _execs: { state: true },
    _imports: { state: true },
    _draft: { state: true },
    _payload: { state: true },
    _busy: { state: true },
    _error: { state: true },
    _info: { state: true },
    _testText: { state: true },
    _testResult: { state: true },
    _open: { state: true },
    _editing: { state: true },
  };

  constructor() {
    super();
    this._tab = "pending";
    this._filter = "";
    this._proposals = [];
    this._sentences = [];
    this._misses = [];
    this._execs = [];
    this._imports = null;
    this._draft = { name: "", description: "", examples: "", cmd_type: "answer", entities: [{ role: "", entity: "" }] };
    this._open = {};
    this._editing = {};
  }

  connectedCallback() {
    super.connectedCallback();
    this._refresh();
  }

  // ------------------------------------------------------------------------------------------ data ----
  async _ws(type, params) {
    this._error = "";
    try {
      return await callWS(this.hass, type, params);
    } catch (err) {
      this._error = err.message || String(err);
      throw err;
    }
  }

  async _run(fn, doneMsg) {
    this._busy = true;
    try {
      await fn();
      if (doneMsg) this._info = doneMsg;
    } catch (_) {
      /* error already shown */
    } finally {
      this._busy = false;
      await this._refresh();
    }
  }

  async _refresh() {
    try {
      this._overview = await callWS(this.hass, "overview");
      const [props, sents] = await Promise.all([
        callWS(this.hass, "proposals/list"),
        callWS(this.hass, "sentences/list", { include_pending: true }),
      ]);
      this._proposals = props;
      this._sentences = sents;
      if (this._tab === "misses") this._misses = await callWS(this.hass, "misses/list");
      if (this._tab === "stats") {
        this._stats = await callWS(this.hass, "stats");
        this._execs = await callWS(this.hass, "executions/list");
      }
    } catch (err) {
      this._error = err.message || String(err);
    }
  }

  _setTab(tab) {
    this._tab = tab;
    this._info = "";
    this._refresh();
  }

  // --------------------------------------------------------------------------------------- render ----
  render() {
    const o = this._overview || {};
    const pending = (o.pending_proposals || 0) + (o.pending_commands || 0);
    return html`
      <div class="wrap">
        <header>
          <h1>Assist Learner</h1>
          <div class="stats">
            <span>LLM fallbacks today: <b>${o.fallbacks_today ?? "-"}</b></span>
            <span>Local hits today: <b>${o.local_hits_today ?? "-"}</b></span>
          </div>
        </header>
        ${this._renderTestBox()}
        ${this._error ? html`<div class="msg err">${this._error}</div>` : nothing}
        ${this._info ? html`<div class="msg ok">${this._info}</div>` : nothing}
        <nav>
          ${[
            ["pending", `Pending${pending ? ` (${pending})` : ""}`],
            ["sentences", "Sentences"],
            ["new", "New command"],
            ["misses", "Misses"],
            ["stats", "Stats"],
          ].map(([id, label]) => html`<button class=${this._tab === id ? "tab on" : "tab"} @click=${() => this._setTab(id)}>${label}</button>`)}
        </nav>
        ${this._busy ? html`<div class="busy">Working...</div>` : nothing}
        ${{ pending: () => this._renderPending(), sentences: () => this._renderSentences(), new: () => this._renderNew(), misses: () => this._renderMisses(), stats: () => this._renderStats() }[this._tab]()}
      </div>
    `;
  }

  _renderTestBox() {
    const r = this._testResult;
    return html`<div class="card testbox">
      <input
        placeholder="Test box: type a sentence to see what would match (nothing is run)"
        .value=${this._testText || ""}
        @input=${(e) => (this._testText = e.target.value)}
        @keydown=${(e) => e.key === "Enter" && this._test()}
      />
      <button @click=${() => this._test()}>Test</button>
      ${r
        ? r.matched
          ? html`<div class="res">Matches <b>${r.title || r.intent}</b> (${r.source}) - slots: <code>${JSON.stringify(r.slots)}</code></div>`
          : html`<div class="res">No local match: this would go to the LLM.</div>`
        : nothing}
    </div>`;
  }

  async _test() {
    if (!this._testText) return;
    this._testResult = await this._ws("sentences/test", { text: this._testText });
  }

  // ------------------------------------------------------------------------------------- pending ----
  _renderPending() {
    const cmds = this._sentences.filter((s) => s.source === "custom" && s.status === "pending");
    return html`
      <div class="row"><button @click=${() => this._run(async () => { const r = await this._ws("run_proposer"); this._info = `${r.created} new proposal(s)`; })}>Run proposer now</button></div>
      <h2>Learned from LLM fallbacks</h2>
      ${this._proposals.length === 0 ? html`<p class="muted">Nothing to review.</p>` : html`<div class="row">
        <button class="sec" @click=${() => this._bulk("approve")}>Approve all (${this._proposals.length})</button>
        <button class="sec" @click=${() => this._bulk("reject")}>Reject all</button></div>`}
      ${this._proposals.map((p) => this._renderProposal(p))}
      <h2>Custom commands awaiting approval</h2>
      ${cmds.length === 0 ? html`<p class="muted">Nothing to review.</p>` : nothing}
      ${cmds.map((c) => this._renderCommandCard(c.id))}
    `;
  }

  _renderProposal(p) {
    const edit = this._editing[p.id];
    return html`<div class="card">
      <div class="title"><code>${edit ?? p.template}</code> <span class="badge">${p.type}</span> <span class="muted">seen ${p.count}x - confidence ${p.confidence}</span></div>
      ${p.diff ? html`<pre>${p.diff}</pre>` : nothing}
      ${p.examples.map((e) => html`<div class="muted">"${e}"</div>`)}
      ${p.warnings.map((w) => html`<div class="warn">⚠ ${w}</div>`)}
      ${p.suggestion ? html`<div class="muted">LLM suggestion: <code>${p.suggestion}</code>
        <button class="chip" @click=${() => (this._editing = { ...this._editing, [p.id]: p.suggestion })}>Use suggestion</button></div>` : nothing}
      ${edit !== undefined ? html`<input .value=${edit} @input=${(e) => (this._editing = { ...this._editing, [p.id]: e.target.value })} />` : nothing}
      <div class="row">
        <button @click=${() => this._run(() => this._ws("proposals/decide", { proposal_id: p.id, decision: "approve", ...(edit !== undefined ? { template: edit } : {}) }), "Approved")}>${edit !== undefined ? "Save & approve" : "Approve"}</button>
        ${edit === undefined ? html`<button class="sec" @click=${() => (this._editing = { ...this._editing, [p.id]: p.template })}>Edit</button>` : nothing}
        <button class="sec" @click=${() => this._run(() => this._ws("proposals/decide", { proposal_id: p.id, decision: "reject" }), "Rejected")}>Reject</button>
      </div>
    </div>`;
  }

  async _bulk(decision) {
    if (!confirm(`${decision === "approve" ? "Approve" : "Reject"} all ${this._proposals.length} proposals?`)) return;
    await this._run(async () => {
      const r = await this._ws("proposals/bulk", { proposal_ids: this._proposals.map((p) => p.id), decision });
      this._info = `${r.done.length} done` + (r.failed.length ? `, ${r.failed.length} failed: ${r.failed.map((f) => f.error).join("; ")}` : "");
    });
  }

  _cmdById(id) {
    return this._sentences.find((s) => s.source === "custom" && s.id === id);
  }

  _renderCommandCard(id) {
    const row = this._cmdById(id);
    if (!row) return nothing;
    const full = this._open[`full_${id}`];
    if (!full) {
      this._ws("commands/get", { item_id: id }).then((c) => (this._open = { ...this._open, [`full_${id}`]: c }));
      return html`<div class="card muted">Loading...</div>`;
    }
    const v = full.validation || {};
    const fb = this._open[`fb_${id}`] || "";
    return html`<div class="card">
      <div class="title"><b>${full.name}</b> <span class="badge">${full.type}</span> <span class="badge">${full.status}</span></div>
      <div class="muted">${full.description}</div>
      <h4>Sentences</h4>
      <ul>${full.sentences.map((s) => html`<li><code>${s}</code></li>`)}</ul>
      ${v.preview ? html`<h4>Preview (live values)</h4><div class="preview">${v.preview}</div>` : nothing}
      ${full.response_template ? html`<h4>Response template</h4><pre>${full.response_template}</pre>` : nothing}
      ${v.calls?.length ? html`<h4>Exact service calls</h4>${v.calls.map((c) => html`<pre>${c.service}\n  targets: ${c.targets.join(", ")}\n  data: ${JSON.stringify(c.data)}</pre>`)}` : nothing}
      ${Object.keys(full.slots || {}).length ? html`<h4>Slots</h4><pre>${JSON.stringify(full.slots, null, 1)}</pre>` : nothing}
      ${(v.flags || []).map((f) => html`<div class="warn">⚠ ${f}</div>`)}
      ${(v.warnings || []).map((f) => html`<div class="warn">⚠ ${f}</div>`)}
      ${(v.errors || []).map((f) => html`<div class="err">✖ ${f}</div>`)}
      ${full.notes ? html`<div class="muted">${full.notes}</div>` : nothing}
      <div class="muted">Entities granted: ${Object.entries(full.entities).map(([r, e]) => `${r} = ${e}`).join(", ")}</div>
      <div class="row">
        <button ?disabled=${!v.ok} @click=${() => this._run(() => this._ws("commands/decide", { item_id: id, decision: "approve" }), "Approved")}>Approve</button>
        <button class="sec" @click=${() => this._run(() => this._ws("commands/decide", { item_id: id, decision: "reject" }), "Moved back to draft")}>Reject</button>
        <button class="sec" @click=${() => this._run(() => this._ws("commands/delete", { item_id: id }), "Deleted")}>Delete</button>
      </div>
      <div class="row">
        <input placeholder="Regenerate with feedback (e.g. also mention humidity)" .value=${fb} @input=${(e) => (this._open = { ...this._open, [`fb_${id}`]: e.target.value })} />
        <button class="sec" ?disabled=${!fb} @click=${() => this._run(async () => { await this._ws("commands/regenerate", { item_id: id, feedback: fb }); this._open = { ...this._open, [`full_${id}`]: null, [`fb_${id}`]: "" }; }, "Regenerated")}>Regenerate</button>
      </div>
    </div>`;
  }

  // ----------------------------------------------------------------------------------- sentences ----
  _renderSentences() {
    const rows = this._sentences.filter((s) => !(s.source === "custom" && s.status === "pending") && (!this._filter || s.source === this._filter));
    return html`
      <div class="row">${["", "learned", "custom", "external"].map((f) => html`<button class=${this._filter === f ? "chip on" : "chip"} @click=${() => (this._filter = f)}>${f || "all"}</button>`)}
        <button class="sec" @click=${() => this._run(async () => (this._imports = await this._ws("import/preview")))}>Preview import of external files</button></div>
      ${this._imports ? this._renderImports() : nothing}
      ${rows.length === 0 ? html`<p class="muted">No sentences yet.</p>` : nothing}
      ${rows.map((r) => this._renderSentenceRow(r))}`;
  }

  _renderImports() {
    return html`<div class="card"><h3>Import preview</h3>
      ${this._imports.length === 0 ? html`<p class="muted">No external sentence files found.</p>` : nothing}
      ${this._imports.map((i) => html`<div class="imp">
        <b>${i.intent}</b> <span class="muted">${i.file}</span>
        ${i.importable ? html`<button class="sec" @click=${() => this._run(async () => { await this._ws("import/apply", { intents: [i.intent] }); this._imports = await this._ws("import/preview"); this._tab = "pending"; }, "Imported as a pending command")}>Import</button>` : html`<span class="warn">stays external: ${i.unmappable}</span>`}
        ${i.collision_note ? html`<div class="warn">⚠ ${i.collision_note}</div>` : nothing}
        ${(i.validation?.errors || []).map((e) => html`<div class="err">✖ ${e}</div>`)}
      </div>`)}
      <button class="sec" @click=${() => (this._imports = null)}>Close</button></div>`;
  }

  _renderSentenceRow(r) {
    const key = `${r.source}_${r.id}`;
    const open = this._open[key];
    return html`<div class="card ${r.enabled === false ? "off" : ""}">
      <div class="title" @click=${() => (this._open = { ...this._open, [key]: !open })}>
        <span class="badge">${r.source}</span> <b>${r.title}</b>
        ${r.status ? html`<span class="badge">${r.status}</span>` : nothing}
        <span class="muted">${r.hits != null ? `${r.hits} hits - last ${fmt(r.last_used)}` : ""}</span>
      </div>
      ${(r.collisions || []).length ? html`<div class="warn">⚠ collides with ${r.collisions.map((c) => c.matches).join(", ")}</div>` : nothing}
      ${open ? html`
        <ul>${r.sentences.map((s) => html`<li><code>${s}</code></li>`)}</ul>
        ${r.response ? html`<pre>${r.response}</pre>` : nothing}
        ${r.file ? html`<div class="muted">${r.file} (read-only; remove it there to avoid duplicates)</div>` : nothing}
        ${r.editable ? this._renderEditor(r) : nothing}` : nothing}
    </div>`;
  }

  _renderEditor(r) {
    const k = `ed_${r.source}_${r.id}`;
    const ed = this._editing[k];
    const toggle = r.source === "learned"
      ? (en) => this._ws("learned/update", { item_id: r.id, enabled: en })
      : (en) => this._ws("commands/decide", { item_id: r.id, decision: en ? "enable" : "disable" });
    return html`
      <div class="row">
        <button class="sec" @click=${() => this._run(() => toggle(!r.enabled))}>${r.enabled ? "Disable" : "Enable"}</button>
        <button class="sec" @click=${() => this._startEdit(r, k)}>Edit</button>
        <button class="sec" @click=${() => this._history(r)}>History</button>
        <button class="sec" @click=${() => this._run(() => this._ws(r.source === "learned" ? "learned/delete" : "commands/delete", { item_id: r.id }), "Deleted")}>Delete</button>
        ${r.source === "custom" && r.type !== "answer" ? html`<button class="sec" @click=${() => this._runOnce(r)}>Run once</button>` : nothing}
      </div>
      ${ed !== undefined ? (typeof ed === "string"
        ? html`<input .value=${ed} @input=${(e) => (this._editing = { ...this._editing, [k]: e.target.value })} /><button @click=${() => this._run(async () => { await this._ws("learned/update", { item_id: r.id, template: ed }); delete this._editing[k]; }, "Saved")}>Save</button>`
        : this._renderCmdEditor(r, k, ed)) : nothing}
      ${this._open[`hist_${r.source}_${r.id}`] ? html`<h4>History</h4>${this._open[`hist_${r.source}_${r.id}`].map((h, i) => html`<div class="muted">${fmt(h.at)} ${h.reason || ""} <button class="chip" @click=${() => this._run(() => this._ws(r.source === "learned" ? "learned/revert" : "commands/revert", { item_id: r.id, index: i }), "Reverted")}>Revert to this</button></div>`)}` : nothing}`;
  }

  async _startEdit(r, k) {
    if (r.source === "learned") {
      this._editing = { ...this._editing, [k]: r.sentences[0] };
      return;
    }
    await this._run(async () => {
      const full = await this._ws("commands/get", { item_id: r.id });
      this._editing = { ...this._editing, [k]: { mode: "builder", form: toForm(full), yaml: "" } };
    });
  }

  _setEd(k, patch) {
    this._editing = { ...this._editing, [k]: { ...this._editing[k], ...patch } };
  }

  async _switchMode(k, ed, mode) {
    if (mode === ed.mode) return;
    try {
      if (mode === "yaml") {
        const { yaml } = await this._ws("commands/to_yaml", { fields: fromForm(ed.form) });
        this._setEd(k, { mode, yaml });
      } else {
        const { fields } = await this._ws("commands/from_yaml", { yaml: ed.yaml });
        this._setEd(k, { mode, form: toForm({ ...fromForm(ed.form), ...fields }) });
      }
    } catch (_) {
      /* error shown; stay in the current mode so nothing is lost */
    }
  }

  async _saveCmd(r, k, ed) {
    await this._run(async () => {
      const fields = ed.mode === "yaml" ? (await this._ws("commands/from_yaml", { yaml: ed.yaml })).fields : fromForm(ed.form);
      await this._ws("commands/update", { item_id: r.id, fields });
      const { [k]: _gone, ...rest } = this._editing;
      this._editing = rest;
    }, "Saved (re-validated)");
  }

  _renderCmdEditor(r, k, ed) {
    const f = ed.form;
    const upd = (patch) => this._setEd(k, { form: { ...f, ...patch } });
    const at = (key, i, patch) => upd({ [key]: f[key].map((x, j) => (j === i ? { ...x, ...patch } : x)) });
    const del = (key, i) => upd({ [key]: f[key].filter((_, j) => j !== i) });
    const roles = f.entities.map((e) => e.role).filter(Boolean);
    const modeBtn = (m, label) => html`<button class=${ed.mode === m ? "chip on" : "chip"} @click=${() => this._switchMode(k, ed, m)}>${label}</button>`;
    return html`<div class="builder">
      <div class="row">${modeBtn("builder", "Builder")}${modeBtn("yaml", "YAML")}</div>
      ${ed.mode === "yaml"
        ? html`<textarea class="yaml" rows="22" spellcheck="false" .value=${ed.yaml} @input=${(e) => this._setEd(k, { yaml: e.target.value })}></textarea>
               <div class="muted">Fields: name, type, sentences, slots, entities, actions, response_template, unavailable_template. Saving re-validates everything.</div>`
        : html`
          <label>Name<input .value=${f.name} @input=${(e) => upd({ name: e.target.value })} /></label>
          <label>Type<select .value=${f.type} @change=${(e) => upd({ type: e.target.value })}>
            ${[["answer", "Answer"], ["action", "Action"], ["answer_and_action", "Answer and action"]].map(([v, l]) => html`<option value=${v} ?selected=${f.type === v}>${l}</option>`)}</select></label>

          <h4>Sentences <span class="muted">(use {slot} for a value; (optional words) and [a|b] alternatives work)</span></h4>
          ${f.sentences.map((x, i) => html`<div class="row"><input .value=${x} @input=${(e) => upd({ sentences: f.sentences.map((y, j) => (j === i ? e.target.value : y)) })} />
            <button class="chip" @click=${() => del("sentences", i)}>✕</button></div>`)}
          <button class="sec" @click=${() => upd({ sentences: [...f.sentences, ""] })}>+ Add sentence</button>

          <h4>Slots <span class="muted">(values the speaker can say)</span></h4>
          ${f.slots.map((x, i) => html`<div class="row slot">
            <input class="role" placeholder="name" .value=${x.name} @input=${(e) => at("slots", i, { name: e.target.value })} />
            <select @change=${(e) => at("slots", i, { type: e.target.value })}>${["number", "choice"].map((t) => html`<option value=${t} ?selected=${x.type === t}>${t}</option>`)}</select>
            ${x.type === "number"
              ? html`<input class="num" type="number" title="min" .value=${x.min} @input=${(e) => at("slots", i, { min: e.target.value })} />
                     <input class="num" type="number" title="max" .value=${x.max} @input=${(e) => at("slots", i, { max: e.target.value })} />
                     <input class="num" type="number" title="step" .value=${x.step} @input=${(e) => at("slots", i, { step: e.target.value })} />`
              : html`<input placeholder="comma separated values" .value=${x.values} @input=${(e) => at("slots", i, { values: e.target.value })} />`}
            <button class="chip" @click=${() => del("slots", i)}>✕</button></div>`)}
          <button class="sec" @click=${() => upd({ slots: [...f.slots, { name: "", type: "number", min: 0, max: 100, step: 1, values: "" }] })}>+ Add slot</button>

          <h4>Entities <span class="muted">(the allow-list this command can read or control)</span></h4>
          ${f.entities.map((e, i) => html`<div class="row ent">
            <input class="role" placeholder="label" .value=${e.role} @input=${(ev) => at("entities", i, { role: ev.target.value })} />
            <al-entity-picker .hass=${this.hass} .value=${e.entity} @picked=${(ev) => at("entities", i, { entity: ev.detail.value })}></al-entity-picker>
            <button class="chip" @click=${() => del("entities", i)}>✕</button></div>`)}
          <button class="sec" @click=${() => upd({ entities: [...f.entities, { role: "", entity: "" }] })}>+ Add entity</button>

          <h4>Actions <span class="muted">(service calls; use {slot} in a value)</span></h4>
          ${f.actions.map((a, i) => html`<div class="card sub">
            <div class="row"><input placeholder="domain.service, e.g. light.turn_on" .value=${a.service} @input=${(e) => at("actions", i, { service: e.target.value })} />
              <button class="chip" @click=${() => del("actions", i)}>✕</button></div>
            <div class="row">Targets: ${roles.length === 0 ? html`<span class="muted">add entities first</span>` : roles.map((role) => html`<label class="inline"><input type="checkbox" .checked=${a.targets.includes(role)}
              @change=${(e) => at("actions", i, { targets: e.target.checked ? [...a.targets, role] : a.targets.filter((t) => t !== role) })} />${role}</label>`)}</div>
            ${a.data.map((d, di) => html`<div class="row"><input class="role" placeholder="field" .value=${d.k} @input=${(e) => at("actions", i, { data: a.data.map((x, j) => (j === di ? { ...x, k: e.target.value } : x)) })} />
              <input placeholder="value" .value=${d.v} @input=${(e) => at("actions", i, { data: a.data.map((x, j) => (j === di ? { ...x, v: e.target.value } : x)) })} />
              <button class="chip" @click=${() => at("actions", i, { data: a.data.filter((_, j) => j !== di) })}>✕</button></div>`)}
            <button class="sec" @click=${() => at("actions", i, { data: [...a.data, { k: "", v: "" }] })}>+ Add field</button></div>`)}
          <button class="sec" @click=${() => upd({ actions: [...f.actions, { service: "", targets: [], data: [] }] })}>+ Add action</button>

          <label>Spoken response (Jinja template)<textarea rows="3" .value=${f.response_template} @input=${(e) => upd({ response_template: e.target.value })}></textarea></label>
          <label>If an entity is unavailable (optional)<textarea rows="2" .value=${f.unavailable_template} @input=${(e) => upd({ unavailable_template: e.target.value })}></textarea></label>`}
      <div class="row"><button @click=${() => this._saveCmd(r, k, ed)}>Save</button>
        <button class="sec" @click=${() => { const { [k]: _gone, ...rest } = this._editing; this._editing = rest; }}>Cancel</button></div>
    </div>`;
  }

  async _history(r) {
    const h = await this._ws(r.source === "learned" ? "learned/history" : "commands/history", { item_id: r.id });
    this._open = { ...this._open, [`hist_${r.source}_${r.id}`]: h };
  }

  async _runOnce(r) {
    if (!confirm(`Run "${r.title}" now? This will call the real services.`)) return;
    const values = {};
    const c = await this._ws("commands/get", { item_id: r.id });
    for (const [name, def] of Object.entries(c.slots || {})) {
      const v = prompt(`Value for ${name}` + (def.type === "number" ? ` (${def.min}-${def.max})` : ` (${def.values.join("/")})`));
      if (v === null) return;
      values[name] = v;
    }
    await this._run(async () => {
      const res = await this._ws("commands/run_once", { item_id: r.id, values });
      this._info = res.ok ? "Ran successfully" : `Failed: ${res.error}`;
    });
  }

  // ----------------------------------------------------------------------------------- new command ----
  _renderNew() {
    const d = this._draft;
    const set = (patch) => (this._draft = { ...d, ...patch });
    const setEnt = (i, patch) => set({ entities: d.entities.map((e, j) => (j === i ? { ...e, ...patch } : e)) });
    const llm = this._overview?.ai_task;
    return html`<div class="card">
      <h2>New command</h2>
      ${llm ? nothing : html`<div class="err">Choose an AI Task entity in the Assist Learner options first.</div>`}
      <label>Name<input .value=${d.name} @input=${(e) => set({ name: e.target.value })} /></label>
      <label>What should it do? (plain English)<textarea rows="3" .value=${d.description} @input=${(e) => set({ description: e.target.value })} placeholder="When I ask what the weather's like outside, answer with the outdoor temperature."></textarea></label>
      <label>Example phrasings (one per line, optional)<textarea rows="2" .value=${d.examples} @input=${(e) => set({ examples: e.target.value })}></textarea></label>
      <label>Type<select .value=${d.cmd_type} @change=${(e) => set({ cmd_type: e.target.value })}>
        <option value="answer">Answer (reads entities and speaks)</option>
        <option value="action">Action (calls services, then confirms)</option>
        <option value="answer_and_action">Answer and action</option></select></label>
      <h4>Entities (this is the allow-list: the command can only read/control these)</h4>
      ${d.entities.map((e, i) => html`<div class="row ent">
        <input class="role" placeholder="label (e.g. outdoor_temp)" .value=${e.role} @input=${(ev) => setEnt(i, { role: ev.target.value })} />
        <al-entity-picker .hass=${this.hass} .value=${e.entity} @picked=${(ev) => setEnt(i, { entity: ev.detail.value })}></al-entity-picker>
        <button class="chip" @click=${() => set({ entities: d.entities.filter((_, j) => j !== i) })}>✕</button></div>`)}
      <button class="sec" @click=${() => set({ entities: [...d.entities, { role: "", entity: "" }] })}>+ Add entity</button>
      ${this._payload ? html`<h4>This exact payload will be sent to the linked LLM${this._overview?.send_states ? " (including current states)" : " (names and units only)"}</h4><pre>${this._payload.instructions}</pre>` : nothing}
      <div class="row">
        <button class="sec" @click=${() => this._preview()}>Preview what will be sent</button>
        <button ?disabled=${!llm} @click=${() => this._generate()}>Generate</button>
      </div></div>`;
  }

  async _createDraft() {
    const d = this._draft;
    const entities = {};
    for (const e of d.entities) if (e.role && e.entity) entities[e.role.replace(/\W+/g, "_").toLowerCase()] = e.entity;
    if (this._draft.id) return this._draft.id;
    const c = await this._ws("commands/create", { name: d.name, description: d.description, entities, examples: lines(d.examples), cmd_type: d.cmd_type });
    this._draft = { ...this._draft, id: c.id };
    return c.id;
  }

  async _preview() {
    await this._run(async () => {
      const id = await this._createDraft();
      this._payload = await this._ws("commands/payload", { item_id: id });
    });
  }

  async _generate() {
    await this._run(async () => {
      const id = await this._createDraft();
      await this._ws("commands/generate", { item_id: id });
      this._draft = { name: "", description: "", examples: "", cmd_type: "answer", entities: [{ role: "", entity: "" }] };
      this._payload = null;
      this._tab = "pending";
      this._info = "Generated. Review it below, then approve.";
    });
  }

  // ------------------------------------------------------------------------------------ misses/stats ----
  _renderMisses() {
    return html`<div class="card"><h2>LLM fallbacks</h2>
      <p class="muted">Raw requests are deleted after the nightly run unless a retention period is set.</p>
      <table><tr><th>When</th><th>Request</th><th>Result</th></tr>
      ${this._misses.map((m) => html`<tr><td>${fmt(m.timestamp)}</td><td>${m.text}</td><td>${m.unalignable ? "unalignable" : m.success ? (m.processed ? "processed" : "new") : "failed"}</td></tr>`)}</table></div>`;
  }

  _renderStats() {
    const s = this._stats;
    if (!s) return html`<p class="muted">Loading...</p>`;
    return html`<div class="card"><h2>Local hits vs LLM fallbacks (daily)</h2>
        <table><tr><th>Date</th><th>Fallbacks</th><th>Local hits</th></tr>${s.daily.map((d) => html`<tr><td>${d.date}</td><td>${d.fallbacks || 0}</td><td>${d.local_hits || 0}</td></tr>`)}</table></div>
      <div class="card"><h2>Per template / command</h2>
        <table>${s.learned.map((t) => html`<tr><td><code>${t.template}</code></td><td>${t.hits}</td></tr>`)}${s.commands.map((c) => html`<tr><td>${c.name}</td><td>${c.hits}</td></tr>`)}</table></div>
      <div class="card"><h2>Action execution log</h2>
        <table><tr><th>Time</th><th>Command</th><th>Calls</th><th>Result</th></tr>${this._execs.map((e) => html`<tr><td>${fmt(e.time)}</td><td>${e.command}</td><td>${e.calls.map((c) => c.service).join(", ")}</td><td>${e.ok ? "ok" : e.error}</td></tr>`)}</table></div>`;
  }

  static styles = css`
    :host { display: block; color: var(--primary-text-color); background: var(--primary-background-color); min-height: 100vh; font-family: var(--paper-font-body1_-_font-family, sans-serif); }
    .wrap { max-width: 1000px; margin: 0 auto; padding: 16px; }
    header { display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap; }
    h1 { margin: 0 0 8px; } h2 { margin: 16px 0 8px; font-size: 1.2em; } h4 { margin: 12px 0 4px; }
    nav { display: flex; gap: 4px; flex-wrap: wrap; margin: 12px 0; border-bottom: 1px solid var(--divider-color); }
    .builder { border-top: 1px solid var(--divider-color); margin-top: 8px; padding-top: 8px; }
    .builder label { display: block; margin: 6px 0; } .builder input, .builder select, .builder textarea { box-sizing: border-box; }
    .builder textarea { width: 100%; } .yaml { font-family: monospace; }
    .row input.num { flex: 0 0 80px; min-width: 80px; width: 80px; } .sub { margin: 6px 0; } label.inline { display: inline-flex; gap: 4px; margin-right: 10px; }
    .tab { background: none; border: none; padding: 10px 14px; cursor: pointer; color: var(--primary-text-color); border-bottom: 3px solid transparent; }
    .tab.on { border-bottom-color: var(--primary-color); font-weight: bold; }
    .card { background: var(--card-background-color, #fff); border-radius: 8px; padding: 12px 16px; margin: 8px 0; box-shadow: var(--ha-card-box-shadow, 0 1px 3px rgba(0,0,0,.2)); }
    .card.off { opacity: .6; } .testbox { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; } .testbox input { flex: 1; min-width: 200px; } .res { width: 100%; }
    .row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin: 8px 0; } .row input { flex: 1; min-width: 160px; }
    .ent al-entity-picker { flex: 2; min-width: 200px; } .ent .role { max-width: 180px; }
    .title { cursor: pointer; } .muted { color: var(--secondary-text-color); font-size: .9em; }
    .badge { background: var(--primary-color); color: var(--text-primary-color, #fff); border-radius: 10px; padding: 1px 8px; font-size: .75em; }
    .warn { color: var(--warning-color, #b36b00); } .err { color: var(--error-color, #b00020); } .msg { padding: 8px 12px; border-radius: 6px; margin: 8px 0; }
    .msg.err { background: color-mix(in srgb, var(--error-color, #b00020) 15%, transparent); } .msg.ok { background: color-mix(in srgb, var(--success-color, #2e7d32) 15%, transparent); }
    .busy { color: var(--secondary-text-color); } .preview { font-style: italic; padding: 4px 0; }
    input, textarea, select { padding: 8px; box-sizing: border-box; width: 100%; font: inherit; color: var(--primary-text-color); background: var(--secondary-background-color); border: 1px solid var(--divider-color); border-radius: 4px; }
    label { display: block; margin: 8px 0; font-size: .9em; } label input, label textarea, label select { margin-top: 4px; }
    button { padding: 8px 14px; border-radius: 6px; border: none; cursor: pointer; background: var(--primary-color); color: var(--text-primary-color, #fff); }
    button.sec { background: var(--secondary-background-color); color: var(--primary-text-color); border: 1px solid var(--divider-color); }
    button.chip { padding: 2px 10px; background: var(--secondary-background-color); color: var(--primary-text-color); border: 1px solid var(--divider-color); border-radius: 12px; }
    button.chip.on { background: var(--primary-color); color: var(--text-primary-color, #fff); } button:disabled { opacity: .5; cursor: default; }
    pre, code { background: var(--secondary-background-color); padding: 2px 4px; border-radius: 4px; white-space: pre-wrap; word-break: break-word; } pre { padding: 8px; }
    table { width: 100%; border-collapse: collapse; } td, th { text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--divider-color); font-size: .9em; }
    .imp { padding: 6px 0; border-bottom: 1px solid var(--divider-color); }
    @media (max-width: 600px) { .wrap { padding: 8px; } }
  `;
}
customElements.define("assist-learner-panel", AssistLearnerPanel);
