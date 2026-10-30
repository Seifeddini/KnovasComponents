/* Einstellungen, "Ansichten und Wissensbasis" (KC-0.1): the registered platform settings.
 *
 * Each setting shows its current value and where it comes from (Installation, Seite, Standard).
 * A value the installation pins is read-only. A secret is a write-only field: the page only says
 * whether it is set. Save sends the changed values with the CSRF token; "Zurücksetzen" removes
 * the page's value. The bundle source and the token each have a Test.
 */
(function () {
    'use strict';
    const box = document.getElementById('platformSettings');
    if (!box) return;
    const list = document.getElementById('platformSettingsList');
    const message = document.getElementById('platformSettingsMessage');
    const csrf = box.dataset.csrf || '';
    const pending = {};                    // key -> new value (null: remove the page's value)
    let state = null;

    function el(tag, attrs, children) {
        const node = document.createElement(tag);
        Object.entries(attrs || {}).forEach(([k, v]) => {
            if (v === undefined || v === null || v === false) return;
            if (k === 'text') node.textContent = v;
            else if (k === 'onclick' || k === 'oninput' || k === 'onchange') node[k] = v;
            else node.setAttribute(k, v === true ? '' : String(v));
        });
        (children || []).forEach((c) => { if (c) node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
        return node;
    }

    async function call(path, body) {
        const init = body === undefined ? { credentials: 'same-origin' }
            : { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body) };
        const resp = await fetch(path, init);
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok && data.success === false) throw new Error(data.error || `Fehler ${resp.status}`);
        return data;
    }

    function origin(s) {
        if (s.source === 'deployment') return `Installation: ${s.reason}`;
        if (s.source === 'page') return `Seite${s.updated_by ? `, geändert von ${s.updated_by}` : ''}${s.updated_at ? ` am ${String(s.updated_at).slice(0, 16).replace('T', ' ')}` : ''}`;
        return 'Standard (config.yaml)';
    }

    function parseRoles(text) {
        const out = {};
        String(text || '').split(';').forEach((part) => {
            const [role, names] = part.split('=');
            if (role && names !== undefined) out[role.trim()] = names.split(',').map((n) => n.trim()).filter(Boolean);
        });
        return out;
    }

    function showResult(target, r) {
        target.textContent = r.ok
            ? (r.counts ? `Geladen: Format ${r.format_version}, ${r.content_hash}, ${r.counts.types} Typen, ${r.counts.processes} Prozesse, ${r.counts.profiles} Profile.`
                : `Lesezugriff auf ${r.repository}: ja${r.write ? ', Schreibrecht: ja' : ''}.`)
            : `Fehler: ${r.error}`;
        target.className = r.ok ? 'platform-settings-ok' : 'platform-settings-error';
    }

    function testButton(s, read) {
        const out = el('span', { role: 'status' });
        const button = el('button', { type: 'button', class: 'btn btn-outline', text: 'Test', onclick: async () => {
            out.textContent = 'wird geprüft …';
            try { showResult(out, await call(`/api/settings/platform/test/${encodeURIComponent(s.key)}`, { value: read() })); }
            catch (err) { showResult(out, { ok: false, error: err.message }); }
        } });
        return [button, out];
    }

    function field(s) {
        const locked = s.pinned || !state.can_edit;
        if (s.kind === 'secret') {
            const input = el('input', { type: 'password', autocomplete: 'new-password', disabled: locked || !s.secrets,
                placeholder: s.set ? 'gesetzt – neuen Wert eingeben, um ihn zu ersetzen' : 'nicht gesetzt',
                oninput: (e) => { if (e.target.value) pending[s.key] = e.target.value; else delete pending[s.key]; } });
            const said = !s.set ? 'nicht gesetzt' : (s.readable === false ? 'gesetzt, aber mit diesem Schlüssel nicht lesbar – neu eingeben' : 'gesetzt');
            const parts = [input, el('span', { class: 'platform-settings-state', text: said }),
                ...testButton(s, () => input.value || null)];
            if (!s.secrets) parts.push(el('p', { class: 'platform-settings-error', text: s.secrets_reason }));
            return parts;
        }
        if (s.kind === 'role_map') {
            const current = parseRoles(s.value);
            const wrap = el('div', { class: 'platform-settings-roles' });
            const choices = state.profiles.map((p) => p.audience || p.id);
            const update = () => {
                const out = {};
                wrap.querySelectorAll('[data-role]').forEach((row) => {
                    const role = row.dataset.role;
                    const picked = Array.from(row.querySelectorAll('input[type=checkbox]:checked')).map((c) => c.value);
                    const free = row.querySelector('input[type=text]');
                    out[role] = picked.concat(free && free.value ? free.value.split(',').map((x) => x.trim()).filter(Boolean) : []);
                });
                pending[s.key] = out;
            };
            state.roles.forEach((role) => {
                const mine = current[role] || [];
                const row = el('div', { 'data-role': role, class: 'platform-settings-role' }, [el('strong', { text: role })]);
                choices.forEach((c) => row.appendChild(el('label', {}, [el('input', { type: 'checkbox', value: c, checked: mine.includes(c), disabled: locked, onchange: update }), ` ${c}`])));
                const extra = mine.filter((m) => !choices.includes(m));
                if (!choices.length || extra.length) {
                    row.appendChild(el('input', { type: 'text', value: extra.join(', '), disabled: locked, 'aria-label': `Profile für ${role}`, oninput: update }));
                }
                wrap.appendChild(row);
            });
            const parts = [wrap];
            if (state.profiles_error) parts.push(el('p', { class: 'platform-settings-note', text: `Profile nicht geladen (${state.profiles_error}): Namen von Hand eintragen.` }));
            return parts;
        }
        const attrs = { type: s.kind === 'number' ? 'number' : 'text', value: s.value === null || s.value === undefined ? '' : s.value,
            disabled: locked, oninput: (e) => { pending[s.key] = e.target.value; } };
        if (s.kind === 'number') Object.assign(attrs, { min: s.minimum, max: s.maximum, step: s.integer ? 1 : 'any' });
        const input = el('input', attrs);
        const parts = [input];
        if (s.key === 'views.config_source') parts.push(...testButton(s, () => input.value || null));
        return parts;
    }

    function render() {
        list.textContent = '';
        state.settings.forEach((s) => {
            const reset = s.source === 'page' && state.can_edit && !s.pinned
                ? el('button', { type: 'button', class: 'btn btn-outline', text: 'Zurücksetzen', onclick: () => save({ [s.key]: null }) }) : null;
            list.appendChild(el('div', { class: 'platform-setting', 'data-key': s.key }, [
                el('label', { class: 'platform-setting-label', text: s.label }),
                el('p', { class: 'platform-settings-note', text: s.help }),
                el('div', { class: 'platform-setting-field' }, [...field(s), reset]),
                el('p', { class: 'platform-setting-origin mono', text: `${origin(s)}${s.scope === 'installation' ? ' · für die ganze Installation' : ' · für diesen Mandanten'}` }),
            ]));
        });
    }

    async function load() {
        try { state = await call('/api/settings/platform'); render(); }
        catch (err) { list.textContent = `Nicht geladen: ${err.message}`; }
    }

    async function save(values) {
        message.textContent = 'wird gespeichert …';
        try {
            const data = await call('/api/settings/platform', { values });
            Object.keys(values).forEach((k) => delete pending[k]);
            state = data;
            render();
            message.textContent = data.changed && data.changed.length ? `Gespeichert: ${data.changed.length} Einstellung(en).` : 'Nichts geändert.';
        } catch (err) { message.textContent = `Nicht gespeichert: ${err.message}`; }
    }

    document.getElementById('platformSettingsSave').addEventListener('click', () => save({ ...pending }));
    load();
}());
