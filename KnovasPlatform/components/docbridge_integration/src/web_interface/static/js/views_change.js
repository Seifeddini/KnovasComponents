/* Ansichten: fix and change from the views (Step 7).
 *
 * Never a brain write. Fixes from the TRACE and the gaps worklist and edits from
 * "Bearbeiten" collect in the user's change set on the server; the review shows
 * each change as a file diff, grouped by the file that owns it (KnowledgeBase's
 * edit map); submitting opens ONE pull request on KnowledgeBase. The browser
 * keeps nothing: the set lives in the Platform, per user.
 */
(function (root) {
    'use strict';
    const { el, clear, api } = root.ViewsCore;

    const toText = (field, value) => {
        if (value === null || value === undefined) return '';
        if (field.kind === 'keys') return (value || []).join(', ');
        if (field.kind === 'risks') return (value || []).map((r) => `${r.id} | ${r.risk} | ${r.mitigation}`).join('\n');
        return String(value);
    };

    const fromText = (field, text) => {
        if (field.kind === 'keys') return text.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);
        if (field.kind === 'risks') {
            return text.split('\n').map((l) => l.trim()).filter(Boolean).map((l) => {
                const [id, risk, mitigation] = l.split('|').map((x) => (x || '').trim());
                return { id, risk: risk || '', mitigation: mitigation || '' };
            });
        }
        return text;
    };

    const CLASS_LABEL = { minor: 'klein', substantive: 'inhaltlich' };

    /** Why an artefact cannot be changed here, in the edit map's words. */
    function lockText(edit) {
        if (!edit) return '';
        if (edit.indirect) return edit.why;
        if (edit.known && !edit.editable) return `Nicht hier änderbar (${edit.reason}). An der Quelle: ${edit.fix_at}`;
        return edit.why || 'Nicht über die Plattform änderbar.';
    }

    function describeOp(op) {
        const what = op.edit_id;
        if (op.kind === 'add') return `${what}: + ${op.target}`;
        if (op.kind === 'remove') return `${what}: − ${op.target}`;
        if (op.kind === 'replace') return `${what}: ${op.target} → ${op.new_target}`;
        return `Feld ${op.field}`;
    }

    /** The user's open change set, as the server holds it. */
    class ChangeSet {
        constructor(page) {
            this.page = page;
            this.data = null;
        }

        count() { return this.data ? this.data.count : 0; }

        ops() { return (this.data && this.data.change_set && this.data.change_set.ops) || []; }

        badge() {
            const b = this.page.$ && this.page.$('viewsChangeCount');
            if (!b) return;
            b.textContent = this.count() ? String(this.count()) : '';
            b.hidden = !this.count();
        }

        async refresh() {
            try { this.data = await api('/api/views/changeset'); } catch (err) { this.data = null; }
            this.badge();
            return this.data;
        }

        /** Operations of this instance already in the set, for "im Änderungssatz" markers. */
        pendingIn(node, editId) {
            return this.ops().filter((o) => o.instance === node && (!editId || o.edit_id === editId));
        }

        async add(payload) {
            const body = Array.isArray(payload) ? { ops: payload } : payload;
            const r = await api('/api/views/changeset/ops', {}, { method: 'POST', body: JSON.stringify(body) });
            this.data = r;
            this.badge();
            return r;
        }

        async drop(id) {
            this.data = await api(`/api/views/changeset/ops/${encodeURIComponent(id)}`, {}, { method: 'DELETE' });
            this.badge();
        }

        async discard() {
            this.data = await api('/api/views/changeset/discard', {}, { method: 'POST', body: '{}' });
            this.badge();
        }
    }

    function flash(box, text, cls) {
        box.appendChild(el('p', { className: cls || 'views-ok', text }));
    }

    /** "+ hinzufügen" and "ersetzen": suggestions on top, then a search over the target type. */
    class Picker {
        constructor(page, ctx) {
            this.page = page;
            this.ctx = ctx;          // {process, node, where, position, artefact, mode, target, targetName}
        }

        async open(q) {
            const c = this.ctx;
            const title = c.mode === 'replace' ? `Ersetzen: ${c.targetName || c.target}` : `${this.page.typeLabel(c.artefact.type)} hinzufügen`;
            const body = this.page.openPanel(title);
            body.appendChild(el('p', { className: 'views-muted', text: 'Lädt Vorschläge…' }));
            try {
                const d = await api('/api/views/pick', { process: c.process, node: c.node, where: c.where, position: c.position, q: q || '' });
                this.draw(body, d, q || '');
            } catch (err) {
                clear(body);
                body.appendChild(el('p', { className: 'views-error', text: err.message }));
            }
        }

        draw(body, d, q) {
            clear(body);
            body.appendChild(el('p', { className: 'views-muted', text: `${d.path} · Die Änderung kommt in den Änderungssatz; KnowledgeBase bleibt die Quelle.` }));
            let reason = null;
            if (this.ctx.mode === 'replace') {
                reason = el('input', { type: 'text', placeholder: 'Warum ist der bisherige Eintrag falsch? (eine Zeile, Pflicht)' });
                body.appendChild(el('label', { className: 'views-change-field' }, [el('span', { text: 'Begründung' }), reason]));
            }
            this.result = el('div', { className: 'views-change-result' });
            const choose = (target) => this.choose(target, reason ? reason.value : '');
            body.appendChild(el('h3', { text: 'Vorschläge' }));
            if (d.suggestions.length) {
                body.appendChild(el('ul', { className: 'views-list views-suggestions' }, d.suggestions.map((s) => el('li', {}, [
                    el('div', {}, [el('strong', { text: s.name }), s.name !== s.target ? el('span', { className: 'views-tech', text: ` ${s.target}` }) : null,
                        el('span', { className: 'views-badge views-badge-muted', title: `Regel ${s.rule}`, text: `Punkte ${s.score}` })]),
                    el('p', { className: 'views-muted', text: s.reason }),
                    el('button', { type: 'button', className: 'views-button', text: 'Übernehmen', onclick: () => choose(s.target) }),
                ]))));
                body.appendChild(el('p', { className: 'views-tech', text: `Vorschläge aus KnowledgeBase (suggestions.json, Stand ${d.suggestions_as_of || '?'}); 3 = aus den eigenen Verknüpfungen, 2 = aus der Nachbarschaft, 1 = aus dem Kalender.` }));
            } else {
                body.appendChild(el('p', { className: 'views-muted', text: d.no_rule ? 'Für diese Lücke kennt KnowledgeBase keine Regel; bitte suchen.' : 'Keine Vorschläge.' }));
            }
            body.appendChild(el('h3', { text: 'Suchen' }));
            const input = el('input', { type: 'search', value: q, placeholder: `${d.type.label} nach Name oder Schlüssel` });
            const form = el('form', { className: 'views-search-inline', onsubmit: (e) => { e.preventDefault(); this.open(input.value); } },
                [input, el('button', { type: 'submit', className: 'views-button', text: 'Suchen' })]);
            body.appendChild(form);
            if (d.results.length) {
                body.appendChild(el('ul', { className: 'views-list' }, d.results.map((r) => el('li', {}, [
                    el('span', { text: r.name }), r.key && !r.name.startsWith(r.key) ? el('span', { className: 'views-tech', text: ` ${r.key}` }) : null,
                    el('button', { type: 'button', className: 'views-button views-button-small', text: 'Übernehmen',
                        onclick: () => (r.key ? choose(r.key) : this.chooseNode(r.id, reason ? reason.value : '')) }),
                ]))));
            } else if (q) {
                body.appendChild(el('p', { className: 'views-muted', text: 'Nichts gefunden.' }));
            }
            body.appendChild(this.result);
        }

        payload(extra) {
            const c = this.ctx;
            const base = { process: c.process, node: c.node, where: c.where, position: c.position };
            if (c.mode === 'replace') return Object.assign(base, { kind: 'replace', target: c.target, target_node: c.targetNode }, extra);
            return Object.assign(base, { kind: 'add' }, extra);
        }

        async submit(payload) {
            clear(this.result);
            try {
                const r = await this.page.changes.add(payload);
                flash(this.result, `Im Änderungssatz (${r.count}). Prüfen und einreichen unter „Änderungen“.`);
                this.page.reload();
            } catch (err) {
                this.result.appendChild(el('p', { className: 'views-error', text: err.message }));
            }
        }

        choose(target, reason) {
            const extra = this.ctx.mode === 'replace' ? { new_target: target, reason } : { target };
            return this.submit(this.payload(extra));
        }

        chooseNode(id, reason) {
            // A node without a key in the index: the server reads its key.
            if (this.ctx.mode === 'replace') return this.submit(this.payload({ new_target: '', new_target_node: id, reason }));
            return this.submit(this.payload({ target_node: id }));
        }
    }

    /** "falsch / entfernen": one line why, then into the change set. */
    function askRemove(page, ctx) {
        const body = page.openPanel(`Entfernen: ${ctx.targetName}`);
        body.appendChild(el('p', { className: 'views-muted', text: `${ctx.artefact.path} · Die Verknüpfung wird in ihrer Quelldatei entfernt (Pull Request); das Wissensnetz folgt nach dem Merge.` }));
        const reason = el('input', { type: 'text', placeholder: 'Warum ist die Verknüpfung falsch? (eine Zeile, Pflicht)' });
        const result = el('div', { className: 'views-change-result' });
        const form = el('form', { className: 'views-form', onsubmit: async (e) => {
            e.preventDefault();
            clear(result);
            try {
                const r = await page.changes.add({ process: ctx.process, node: ctx.node, where: ctx.where, position: ctx.position,
                    kind: 'remove', target_node: ctx.targetNode, reason: reason.value });
                flash(result, `Im Änderungssatz (${r.count}).`);
                page.reload();
            } catch (err) {
                result.appendChild(el('p', { className: 'views-error', text: err.message }));
            }
        } }, [el('label', { className: 'views-change-field' }, [el('span', { text: 'Begründung' }), reason]),
            el('button', { type: 'submit', className: 'views-button', text: 'In den Änderungssatz' })]);
        body.appendChild(form);
        body.appendChild(result);
    }

    /** "Bearbeiten": exactly the template's fields; edits go into the change set. */
    class PlanForm {
        constructor(page, node, process) {
            this.page = page;
            this.node = node;
            this.process = process;
            this.inputs = {};
        }

        async open() {
            this.body = this.page.openPanel('Plan bearbeiten');
            this.body.appendChild(el('p', { className: 'views-muted', text: 'Lädt den Plan aus KnowledgeBase…' }));
            try {
                this.form = await api('/api/views/change', { process: this.process, node: this.node });
                this.draw();
            } catch (err) {
                clear(this.body);
                this.body.appendChild(el('p', { className: 'views-error', text: err.message }));
            }
        }

        draw() {
            const f = this.form;
            const body = clear(this.body);
            body.appendChild(el('p', { className: 'views-muted', text:
                `${f.plan.key} · ${f.plan.path} · Status ${f.plan.status || '—'} (Schritt ${f.plan.step || '—'}). ` +
                'Änderungen kommen in den Änderungssatz; eingereicht wird ein Pull Request auf KnowledgeBase.' }));
            const formEl = el('form', { className: 'views-form views-change-form', onsubmit: (e) => { e.preventDefault(); this.save(); } });
            f.fields.forEach((field) => {
                const route = f.routes[field.id] || {};
                const label = el('label', { className: 'views-change-field' }, [
                    el('span', {}, [field.label, route.class ? el('span', { className: 'views-badge views-badge-muted', text: CLASS_LABEL[route.class] }) : null]),
                ]);
                if (route.locked) {
                    label.appendChild(el('input', { type: 'text', disabled: true, value: '' }));
                    label.appendChild(el('span', { className: 'views-lock', text: `🔒 ${route.locked}` }));
                } else {
                    const multi = ['textarea', 'risks'].includes(field.kind);
                    const input = el(multi ? 'textarea' : 'input', multi ? { rows: field.kind === 'risks' ? 4 : 5 } : { type: 'text' });
                    input.value = toText(field, f.values[field.id]);
                    this.inputs[field.id] = { field, input };
                    label.appendChild(input);
                    if (f.absent.includes(field.id)) label.appendChild(el('span', { className: 'views-muted', text: 'Nicht in dieser Datei; wird an der Stelle der Vorlage eingefügt.' }));
                    if (field.kind === 'keys') label.appendChild(el('span', { className: 'views-muted', text: 'Schlüssel, durch Komma getrennt (z. B. CAP-05, GOAL-2026-Q4-…, L2-…).' }));
                    if (field.kind === 'risks') label.appendChild(el('span', { className: 'views-muted', text: 'Eine Zeile je Risiko: RISK-nn | Risiko | Massnahme' }));
                }
                formEl.appendChild(label);
            });
            formEl.appendChild(el('button', { type: 'submit', className: 'views-button', text: 'In den Änderungssatz übernehmen' }));
            body.appendChild(formEl);
            this.result = el('div', { className: 'views-change-result' });
            body.appendChild(this.result);
        }

        values() {
            const out = {};
            Object.entries(this.inputs).forEach(([id, { field, input }]) => { out[id] = fromText(field, input.value); });
            return out;
        }

        async save() {
            const box = clear(this.result);
            try {
                const r = await api('/api/views/changeset/fields', {}, { method: 'POST',
                    body: JSON.stringify({ process: this.process, node: this.node, values: this.values() }) });
                this.page.changes.data = r;
                this.page.changes.badge();
                flash(box, `${r.added.length} ${r.added.length === 1 ? 'Änderung' : 'Änderungen'} im Änderungssatz (${r.count}). Prüfen und einreichen unter „Änderungen“.`);
            } catch (err) {
                box.appendChild(el('p', { className: 'views-error', text: err.message }));
            }
        }
    }

    /** "Änderungen": the review screen. */
    async function renderReview(box, d, page) {
        page.changes.data = d;
        page.changes.badge();
        if (!d.change_set || !d.count) {
            box.appendChild(el('p', { className: 'views-empty', text: 'Der Änderungssatz ist leer. Lücken im Ablauf oder unter „Lücken“ schliessen, oder einen Plan bearbeiten.' }));
            return;
        }
        const reasons = Object.assign({}, d.change_set.reasons || {});
        const holder = el('div', { className: 'views-review' });
        box.appendChild(holder);
        const run = async (path) => {
            clear(holder);
            holder.appendChild(el('p', { className: 'views-muted', text: 'Prüft den Änderungssatz gegen KnowledgeBase…' }));
            try {
                const r = await api(path || '/api/views/changeset/review', {}, { method: 'POST', body: JSON.stringify({ reasons }) });
                clear(holder);
                drawReview(holder, r, page, reasons, run);
            } catch (err) {
                clear(holder);
                holder.appendChild(el('p', { className: 'views-error', text: err.message }));
            }
        };
        await run();
    }

    function drawReview(box, r, page, reasons, run) {
        const okOps = r.ops.filter((o) => o.ok);
        box.appendChild(el('div', { className: 'views-review-head' }, [
            el('h2', { text: `Änderungssatz: ${r.ops.length} ${r.ops.length === 1 ? 'Änderung' : 'Änderungen'}, ${r.files.length} ${r.files.length === 1 ? 'Datei' : 'Dateien'}` }),
            el('p', { className: 'views-muted', text: `Basis ${r.base.branch} @ ${r.base.commit.slice(0, 12)}${r.behind ? ` · master ist inzwischen bei ${r.head.slice(0, 12)}` : ''}` }),
            el('div', { className: 'views-actions' }, [
                r.behind ? el('button', { type: 'button', className: 'views-button', text: 'Neu aufsetzen (Rebase)', onclick: () => run('/api/views/changeset/rebase') }) : null,
                el('button', { type: 'button', className: 'views-button views-button-quiet', text: 'Verwerfen',
                    onclick: async () => { await page.changes.discard(); page.reload(); } }),
            ]),
        ]));
        if (r.conflicts.length) {
            const sec = el('section', { className: 'views-review-conflicts' }, [
                el('h3', { text: 'Konflikt: diese Dateien haben sich auf master geändert, seit der Änderungssatz begonnen wurde' }),
                el('p', { className: 'views-muted', text: 'Neu aufsetzen übernimmt den aktuellen Stand und wendet die Änderungen erneut an; was nicht mehr passt, wird genannt.' }),
            ]);
            r.conflicts.forEach((c) => sec.appendChild(el('details', { open: true }, [el('summary', { text: c.path }), el('pre', { className: 'views-diff', text: c.diff })])));
            box.appendChild(sec);
        }
        r.instances.forEach((inst) => {
            const sec = el('section', { className: 'views-review-instance' }, [
                el('h3', {}, [`${inst.key} — ${inst.name}`, inst.class ? el('span', { className: `views-badge views-class-${inst.class}`, text: CLASS_LABEL[inst.class] }) : null]),
            ]);
            const ul = el('ul', { className: 'views-list views-review-ops' });
            r.ops.filter((o) => o.instance === inst.node).forEach((o) => ul.appendChild(el('li', { className: o.ok ? '' : 'views-flag' }, [
                el('span', { className: `views-badge views-class-${o.class}`, text: CLASS_LABEL[o.class] }),
                el('span', { text: ` ${describeOp(o)}` }),
                o.reason ? el('div', { className: 'views-muted', text: `Begründung: ${o.reason}` }) : null,
                o.suggestion ? el('div', { className: 'views-muted', text: `Vorschlag ${o.suggestion.rule} (Punkte ${o.suggestion.score}): ${o.suggestion.reason}` }) : null,
                o.ok ? null : el('div', { className: 'views-error', text: o.error }),
                o.permitted === false ? el('div', { className: 'views-error', text: 'Nicht erlaubt: inhaltliche Änderungen nur für Verantwortliche und Administratoren.' }) : null,
                el('button', { type: 'button', className: 'views-button views-button-small', text: 'entfernen',
                    onclick: async () => { await page.changes.drop(o.id); run(); } }),
            ])));
            sec.appendChild(ul);
            if (inst.rollback) {
                sec.appendChild(el('p', { className: 'views-flag', text: `Inhaltliche Änderung nach „${inst.rollback.from_step}“: Status zurück auf „${inst.rollback.status}“ (Schritt ${inst.rollback.to_step}). Erneut zu bestehen: ${inst.rules.join(', ') || '—'}.` }));
                const why = el('textarea', { rows: 3, placeholder: 'Begründung für den Entscheidungseintrag (Pflicht, mindestens 20 Zeichen)' });
                why.value = reasons[inst.key] || '';
                why.addEventListener('change', () => { reasons[inst.key] = why.value; run(); });
                sec.appendChild(el('label', { className: 'views-change-field' }, [
                    el('span', { text: `Entscheidungseintrag ${inst.decision ? inst.decision.split('/').pop() : ''}` }), why]));
            }
            const imp = inst.impact || {};
            sec.appendChild(el('p', { className: 'views-muted', text: `Wirkung: ${(imp.components || []).length} Komponenten, ${(imp.invariants || []).length} Invarianten, ${(imp.risks || []).length} Risiken, ${(imp.decisions || []).length} Entscheidungen.` }));
            box.appendChild(sec);
        });
        const files = el('section', { className: 'views-review-files' }, [el('h3', { text: 'Dateien (je Quelldatei laut Edit-Map)' })]);
        const label = (id) => {
            if (id.startsWith('rollback:')) return `Status-Rücksetzung ${id.slice(9)}`;
            if (id.startsWith('decision:')) return `Entscheidungseintrag ${id.slice(9)}`;
            const o = r.ops.find((x) => x.id === id);
            return o ? describeOp(o) : id;
        };
        r.files.forEach((f) => files.appendChild(el('details', {}, [
            el('summary', {}, [f.path, f.new ? el('span', { className: 'views-badge', text: 'neu' }) : null,
                el('span', { className: 'views-tech', text: ` · ${f.ops.map(label).join(' · ')}` })]),
            el('pre', { className: 'views-diff', text: f.diff }),
        ])));
        box.appendChild(files);
        const blockers = [];
        if (r.conflicts.length) blockers.push('erst neu aufsetzen');
        if (r.missing_reasons.length) blockers.push(`Begründung fehlt für ${r.missing_reasons.join(', ')}`);
        if (okOps.length !== r.ops.length) blockers.push('nicht anwendbare Änderungen entfernen');
        if (!r.permitted) blockers.push('nicht jede Änderung ist erlaubt');
        if (!r.can_write) blockers.push('kein GitHub-Zugang konfiguriert (nur Vorschau)');
        const result = el('div', { className: 'views-change-result' });
        box.appendChild(el('div', { className: 'views-actions' }, [
            el('button', { type: 'button', className: 'views-button views-button-primary', text: 'Pull Request erstellen',
                disabled: !!blockers.length, onclick: async () => {
                    clear(result);
                    result.appendChild(el('p', { className: 'views-muted', text: 'Erstellt den Pull Request…' }));
                    try {
                        const s = await api('/api/views/changeset/submit', {}, { method: 'POST', body: JSON.stringify({ reasons }) });
                        clear(result);
                        result.appendChild(el('p', {}, ['Pull Request erstellt: ', el('a', { href: s.pr.url, target: '_blank', rel: 'noopener noreferrer', text: `#${s.pr.number}` }),
                            '. Nach dem Merge aktualisiert der Loader das Wissensnetz; bis dahin zeigen die Ansichten „Änderung ausstehend“.']));
                        page.changes.refresh();
                    } catch (err) {
                        clear(result);
                        result.appendChild(el('p', { className: 'views-error', text: err.message }));
                    }
                } }),
            blockers.length ? el('span', { className: 'views-muted', text: ` Noch nicht möglich: ${blockers.join('; ')}.` }) : null,
        ]));
        box.appendChild(result);
    }

    root.ViewsChange = { ChangeSet, Picker, PlanForm, askRemove, renderReview, lockText, describeOp, toText, fromText };
}(typeof window !== 'undefined' ? window : globalThis));
