/* Ansichten: change requests from the TRACE ("Änderung vorschlagen", Step 7).
 *
 * Never a brain write: the form proposes a change to the plan file in
 * KnowledgeBase; the server turns it into a pull request. Fields come from the
 * server (what the plan file holds); a conflict shows the diff and carries the
 * user's own edits onto the current version.
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

    class ChangeForm {
        constructor(page, node, process) {
            this.page = page;
            this.node = node;
            this.process = process;
            this.inputs = {};
        }

        async open() {
            this.body = this.page.openPanel('Änderung vorschlagen');
            this.body.appendChild(el('p', { className: 'views-muted', text: 'Lädt den Plan aus KnowledgeBase…' }));
            try {
                this.form = await api('/api/views/change', { process: this.process, node: this.node });
                this.original = JSON.parse(JSON.stringify(this.form.values));
                this.draw();
            } catch (err) {
                clear(this.body);
                this.body.appendChild(el('p', { className: 'views-error', text: err.message }));
            }
        }

        draw(carried) {
            const f = this.form;
            const body = clear(this.body);
            body.appendChild(el('p', { className: 'views-muted', text:
                `${f.plan.key} · ${f.plan.path} · Status ${f.plan.status || '—'} (Schritt ${f.plan.step || '—'}). ` +
                'Die Änderung wird ein Pull Request auf die Plan-Datei; das Wissensnetz folgt nach dem Merge.' }));
            if (!f.can_write) body.appendChild(el('p', { className: 'views-flag', text: 'Nur Vorschau: kein GitHub-Zugang konfiguriert.' }));
            const formEl = el('form', { className: 'views-form views-change-form', onsubmit: (e) => { e.preventDefault(); this.preview(); } });
            f.fields.forEach((field) => {
                const unmapped = f.unmapped[field.id];
                const cls = f.classes.substantive.includes(field.id) ? 'inhaltlich' : (f.classes.minor.includes(field.id) ? 'klein' : '');
                const label = el('label', { className: 'views-change-field' }, [
                    el('span', {}, [field.label, cls ? el('span', { className: 'views-badge views-badge-muted', text: cls }) : null]),
                ]);
                if (unmapped) {
                    label.appendChild(el('input', { type: 'text', disabled: true, value: '' }));
                    label.appendChild(el('span', { className: 'views-muted', text: unmapped }));
                } else {
                    const value = carried && field.id in carried ? carried[field.id] : f.values[field.id];
                    const multi = ['textarea', 'risks'].includes(field.kind);
                    const input = el(multi ? 'textarea' : 'input', multi ? { rows: field.kind === 'risks' ? 4 : 5 } : { type: 'text' });
                    input.value = toText(field, value);
                    this.inputs[field.id] = { field, input };
                    label.appendChild(input);
                    if (f.absent.includes(field.id)) label.appendChild(el('span', { className: 'views-muted', text: 'Nicht in dieser Datei; wird an der Stelle der Vorlage eingefügt.' }));
                    if (field.kind === 'keys') label.appendChild(el('span', { className: 'views-muted', text: 'Schlüssel, durch Komma getrennt (z. B. CAP-05, GOAL-2026-Q4-…, L2-…).' }));
                    if (field.kind === 'risks') label.appendChild(el('span', { className: 'views-muted', text: 'Eine Zeile je Risiko: RISK-nn | Risiko | Massnahme' }));
                }
                formEl.appendChild(label);
            });
            this.why = el('textarea', { rows: 3, placeholder: 'Warum? (Pflicht bei inhaltlichen Änderungen; wird der Entscheidungseintrag)' });
            formEl.appendChild(el('label', { className: 'views-change-field' }, [el('span', { text: 'Begründung' }), this.why]));
            formEl.appendChild(el('button', { type: 'submit', className: 'views-button', text: 'Vorschau' }));
            body.appendChild(formEl);
            this.result = el('div', { className: 'views-change-result' });
            body.appendChild(this.result);
        }

        values() {
            const out = {};
            Object.entries(this.inputs).forEach(([id, { field, input }]) => { out[id] = fromText(field, input.value); });
            return out;
        }

        /** The fields the user changed against the form as it was opened. */
        edits() {
            const now = this.values();
            const out = {};
            Object.keys(now).forEach((k) => {
                if (JSON.stringify(now[k]) !== JSON.stringify(fromText(this.inputs[k].field, toText(this.inputs[k].field, this.original[k])))) out[k] = now[k];
            });
            return out;
        }

        payload() {
            return { process: this.process, node: this.node, base: this.form.base, values: this.values(), why: this.why.value };
        }

        async preview() {
            const box = clear(this.result);
            box.appendChild(el('p', { className: 'views-muted', text: 'Prüft…' }));
            try {
                const p = await api('/api/views/change/preview', {}, { method: 'POST', body: JSON.stringify(this.payload()) });
                clear(box);
                this.drawPreview(box, p);
            } catch (err) {
                clear(box);
                this.drawError(box, err);
            }
        }

        drawError(box, err) {
            box.appendChild(el('p', { className: 'views-error', text: err.message }));
            const c = err.body;
            if (c && c.conflict) {
                if (c.diff) box.appendChild(el('pre', { className: 'views-diff', text: c.diff }));
                box.appendChild(el('button', { type: 'button', className: 'views-button', text: 'Meine Änderungen auf den aktuellen Stand übertragen',
                    onclick: () => { const mine = this.edits(); this.form = c.current; this.original = JSON.parse(JSON.stringify(c.current.values)); this.draw(mine); } }));
            }
        }

        drawPreview(box, p) {
            const imp = p.impact;
            const klass = p.class === 'substantive' ? 'Inhaltliche Änderung' : 'Kleine Änderung';
            box.appendChild(el('h3', { text: `${klass}: ${p.fields.join(', ')}` }));
            const facts = el('ul', { className: 'views-list' }, [
                el('li', { text: p.new_status ? `Plan geht zurück auf Status „${p.new_status}“ (Schritt ${imp.returns_to}).` : 'Der Schritt des Plans bleibt.' }),
                el('li', { text: imp.rules.length ? `Regeln müssen wieder bestehen: ${imp.rules.join(', ')}` : 'Keine Regel muss neu bestehen.' }),
                imp.size_note ? el('li', { className: 'views-muted', text: imp.size_note }) : null,
                el('li', { text: `Komponenten: ${imp.components.map((c) => `${c.name}${c.added ? ' (neu)' : ''}${c.removed ? ' (entfällt)' : ''}`).join(', ') || '—'}` }),
                imp.unknown_components.length ? el('li', { className: 'views-flag', text: `Nicht im Wissensnetz gefunden: ${imp.unknown_components.join(', ')}` }) : null,
                el('li', { text: `Berührt: ${imp.invariants.length} Invarianten, ${imp.risks.length} Risiken, ${imp.decisions.length} Entscheidungen` }),
                el('li', { text: `Dateien: ${p.files.join(', ')}` }),
            ]);
            box.appendChild(facts);
            Object.entries(p.diffs).forEach(([path, diff]) => {
                box.appendChild(el('details', {}, [el('summary', { text: path }), el('pre', { className: 'views-diff', text: diff })]));
            });
            if (!p.permitted) {
                box.appendChild(el('p', { className: 'views-flag', text: p.class === 'substantive'
                    ? 'Inhaltliche Änderungen beantragen nur die Plan-Verantwortlichen und Administratoren.'
                    : 'Diese Änderung darf nur beantragen, wer den Plan bearbeiten darf.' }));
            }
            box.appendChild(el('button', { type: 'button', className: 'views-button', text: 'Pull Request erstellen',
                disabled: !(p.permitted && p.can_write), onclick: () => this.submit() }));
        }

        async submit() {
            const box = clear(this.result);
            box.appendChild(el('p', { className: 'views-muted', text: 'Erstellt den Pull Request…' }));
            try {
                const r = await api('/api/views/change', {}, { method: 'POST', body: JSON.stringify(this.payload()) });
                clear(box);
                box.appendChild(el('p', {}, ['Pull Request erstellt: ', el('a', { href: r.pr.url, target: '_blank', rel: 'noopener noreferrer', text: `#${r.pr.number}` })]));
                this.page.reload();
            } catch (err) {
                clear(box);
                this.drawError(box, err);
            }
        }
    }

    /** "Änderung beantragt: PR #n" on the TRACE until the loader has carried it in. */
    async function pendingBadge(container, node) {
        try {
            const d = await api('/api/views/change/pending', { node });
            d.pending.forEach((p) => container.appendChild(el('a', { className: 'views-badge views-badge-pending', href: p.pr_url || '#',
                target: '_blank', rel: 'noopener noreferrer',
                text: `Änderung beantragt: PR #${p.pr_number} (${p.state === 'merged' ? 'gemergt, wartet auf den Loader' : 'offen'})` })));
        } catch (err) { /* without accounts there are no requests */ }
    }

    root.ViewsChange = { ChangeForm, pendingBadge, toText, fromText };
}(typeof window !== 'undefined' ? window : globalThis));
