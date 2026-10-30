/* Sync von GitHub (KC-0.2): "Neuen Stand holen", "Probelauf", "Anwenden", the progress of a running sync and the
 * history. Probelauf and Anwenden run in the background; the page polls the run until it ends. Counts only: no file
 * names, no values, and never the token (the server sends only "gesetzt" / "nicht gesetzt").
 */
(function () {
    'use strict';
    const root = document.getElementById('brainSync');
    if (!root) return;
    const csrf = root.dataset.csrf || '';
    const $ = (id) => document.getElementById(id);
    const message = $('syncMessage');

    function el(tag, attrs, children) {
        const node = document.createElement(tag);
        Object.entries(attrs || {}).forEach(([k, v]) => {
            if (v === undefined || v === null || v === false) return;
            if (k === 'text') node.textContent = v; else node.setAttribute(k, v === true ? '' : String(v));
        });
        (children || []).forEach((c) => { if (c) node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
        return node;
    }

    async function call(path, method) {
        const init = method === 'POST'
            ? { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRF-Token': csrf } }
            : { credentials: 'same-origin' };
        const resp = await fetch(path, init);
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(data.error || `Fehler ${resp.status}`);
        return data;
    }

    const when = (t) => (t ? String(t).slice(0, 19).replace('T', ' ') : '–');
    const short = (sha) => (sha ? String(sha).slice(0, 12) : '–');
    const STATUS = { ok: 'erfolgreich', failed: 'fehlgeschlagen', refused: 'verweigert', running: 'läuft' };
    const KIND = { probe: 'Probelauf', apply: 'Anwenden' };

    function countsTable(counts) {
        const ids = Object.keys(counts || {});
        if (!ids.length) return el('p', { class: 'settings-note', text: 'Keine Zuordnungen.' });
        const head = ['Zuordnung', 'Quelle', 'Kandidaten', 'neu', 'geändert', 'entfernt', 'unverändert', 'Drift', 'Zustand'];
        return el('table', { class: 'sync-table' }, [
            el('thead', {}, [el('tr', {}, head.map((h) => el('th', { text: h })))]),
            el('tbody', {}, ids.map((id) => {
                const c = counts[id];
                const condition = c.guard_tripped ? 'blockiert (Löschschutz)' : (c.unreadable ? `blockiert (${c.unreadable} nicht lesbar)` : 'bereit');
                return el('tr', {}, [id, c.source_items, c.candidates, c.add, c.change, c.retire, c.unchanged, c.drift, condition]
                    .map((v) => el('td', { text: String(v) })));
            })),
        ]);
    }

    function render(state) {
        const s = state.settings;
        const list = $('syncSettings');
        list.textContent = '';
        [['Repository', s.repository || '–'], ['Branch', s.branch || '–'], ['Konfiguration', s.config_path || '–'],
            ['Lese-Token', s.token], ['Paket', state.package ? `brain-runner ${state.package}` : 'nicht installiert']]
            .forEach(([k, v]) => { list.appendChild(el('dt', { text: k })); list.appendChild(el('dd', { class: 'mono', text: v })); });
        const cur = state.current;
        $('syncCurrent').textContent = '';
        $('syncCurrent').appendChild(cur
            ? el('p', { text: `Commit ${short(cur.commit)} · Bündel ${String(cur.content_hash || '').slice(0, 19)} · brain-runner ${cur.package || '?'}` })
            : el('p', { class: 'settings-note', text: 'Noch kein erfolgreicher Sync.' }));
        const hist = state.history || [];
        $('syncHistory').textContent = '';
        $('syncHistory').appendChild(hist.length ? el('table', { class: 'sync-table' }, [
            el('thead', {}, [el('tr', {}, ['Zeit', 'Schritt', 'Commit', 'von', 'Dateien geholt', 'Dauer', 'Ergebnis'].map((h) => el('th', { text: h })))]),
            el('tbody', {}, hist.map((r) => el('tr', {}, [when(r.started_at), KIND[r.kind] || r.kind, short(r.commit), r.started_by || '–',
                r.files_fetched === null ? '–' : String(r.files_fetched), r.duration_ms === null ? '–' : `${(r.duration_ms / 1000).toFixed(1)} s`,
                `${STATUS[r.status] || r.status}${r.result ? `: ${r.result}` : ''}`].map((v) => el('td', { text: String(v) }))))),
        ]) : el('p', { class: 'settings-note', text: 'Noch kein Sync.' }));
        const latestProbe = hist.find((r) => r.kind === 'probe');
        if (latestProbe && latestProbe.status === 'ok') {
            $('syncCounts').textContent = '';
            $('syncCounts').appendChild(el('h3', { text: `Probelauf für ${short(latestProbe.commit)}: ${latestProbe.files_fetched} Dateien geholt` }));
            $('syncCounts').appendChild(countsTable(latestProbe.counts));
        }
        const running = state.running;
        ['syncCheck', 'syncProbe', 'syncApply'].forEach((id) => { $(id).disabled = !!running; });
        if (running) { message.textContent = `${KIND[running.kind]} läuft: ${running.progress || '…'}`; follow(running.id); }
    }

    async function load() {
        try { render(await call('/api/sync')); } catch (err) { message.textContent = `Nicht geladen: ${err.message}`; }
    }

    let following = null;
    async function follow(runId) {
        if (following === runId) return;
        following = runId;
        while (following === runId) {
            await new Promise((r) => setTimeout(r, 1500));
            try {
                const { run } = await call(`/api/sync/runs/${runId}`);
                message.textContent = run.status === 'running' ? `${KIND[run.kind]} läuft: ${run.progress || '…'}`
                    : `${KIND[run.kind]} ${STATUS[run.status] || run.status}${run.result ? `: ${run.result}` : ''}`;
                if (run.status !== 'running') { following = null; await load(); }
            } catch (err) { message.textContent = err.message; following = null; }
        }
    }

    $('syncCheck').addEventListener('click', async () => {
        message.textContent = 'Stand wird geholt …';
        try {
            const h = await call('/api/sync/check', 'POST');
            $('syncHead').textContent = '';
            $('syncHead').appendChild(el('p', { text: `${h.repository} @ ${h.branch}: Commit ${short(h.commit)} vom ${when(h.date)} – „${h.message || ''}“` }));
            $('syncHead').appendChild(el('p', { class: 'settings-note', text: h.changed_mapped_files === null
                ? (h.last_commit ? 'Geänderte Dateien nicht bestimmbar (zu viele Unterschiede): der Probelauf liest neu.' : 'Noch kein Sync: alle Dateien sind neu.')
                : `${h.changed_mapped_files} geänderte Quelldateien seit dem letzten Sync (${short(h.last_commit)}).` }));
            message.textContent = '';
        } catch (err) { message.textContent = err.message; }
    });
    $('syncProbe').addEventListener('click', async () => {
        try { const { run_id } = await call('/api/sync/probe', 'POST'); message.textContent = 'Probelauf gestartet …'; follow(run_id); }
        catch (err) { message.textContent = err.message; }
    });
    $('syncApply').addEventListener('click', async () => {
        try { const { run_id } = await call('/api/sync/apply', 'POST'); message.textContent = 'Anwenden gestartet …'; follow(run_id); }
        catch (err) { message.textContent = err.message; await load(); }
    });
    load();
}());
