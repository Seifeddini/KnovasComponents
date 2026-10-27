/* Ansichten: shared helpers for every render kind.
 *
 * No markup sinks: every element is built with el(), every text goes in as a
 * text node. Nothing from the brain is stored in the browser (plan D1): the
 * page keeps what it shows in memory and drops it on reload.
 */
(function (root) {
    'use strict';

    const PALETTE = [
        '#1A45C7', '#1a6b4a', '#9a3412', '#6d28d9', '#0f766e', '#b45309',
        '#be123c', '#4d7c0f', '#0369a1', '#7c2d12', '#a21caf', '#334155',
    ];
    const SHAPES = ['ellipse', 'round-rectangle', 'diamond', 'hexagon', 'triangle',
        'round-pentagon', 'star', 'barrel', 'round-tag', 'vee', 'rhomboid', 'octagon'];

    function hash(text) {
        let h = 2166136261;
        for (let i = 0; i < text.length; i += 1) {
            h ^= text.charCodeAt(i);
            h = Math.imul(h, 16777619);
        }
        return h >>> 0;
    }

    /** Colour and shape of a type: stable across pages, overridable per view. */
    function typeStyle(typeName, overrides) {
        const own = (overrides || {})[typeName] || {};
        const h = hash(String(typeName || ''));
        return {
            color: own.color || PALETTE[h % PALETTE.length],
            shape: own.shape || SHAPES[Math.floor(h / PALETTE.length) % SHAPES.length],
        };
    }

    function el(tag, attrs, children) {
        const node = document.createElement(tag);
        Object.entries(attrs || {}).forEach(([key, value]) => {
            if (value === undefined || value === null || value === false) return;
            if (key === 'text') node.textContent = String(value);
            else if (key === 'className') node.className = value;
            else if (key === 'dataset') Object.assign(node.dataset, value);
            else if (key.startsWith('on') && typeof value === 'function') {
                node.addEventListener(key.slice(2), value);
            } else node.setAttribute(key, value === true ? '' : String(value));
        });
        [].concat(children || []).forEach((child) => {
            if (child === null || child === undefined || child === false) return;
            node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
        });
        return node;
    }

    function clear(node) {
        while (node && node.firstChild) node.removeChild(node.firstChild);
        return node;
    }

    /** A small SVG swatch of a type's colour and shape for legends and lists. */
    function swatch(typeName, overrides) {
        const style = typeStyle(typeName, overrides);
        const ns = 'http://www.w3.org/2000/svg';
        const svg = document.createElementNS(ns, 'svg');
        svg.setAttribute('viewBox', '0 0 16 16');
        svg.setAttribute('class', 'views-swatch');
        svg.setAttribute('aria-hidden', 'true');
        const paths = {
            'ellipse': 'M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1z',
            'diamond': 'M8 1l7 7-7 7-7-7z',
            'triangle': 'M8 1l7 14H1z',
            'hexagon': 'M4 1h8l4 7-4 7H4L0 8z',
        };
        const shape = document.createElementNS(ns, 'path');
        shape.setAttribute('d', paths[style.shape] || 'M2 2h12v12H2z');
        shape.setAttribute('fill', style.color);
        svg.appendChild(shape);
        return svg;
    }

    /** "22 Alloy-Prüfung: 20 passed, 2 failed" — the values as the nodes hold them. */
    function chipLabel(chip, typeLabel) {
        const head = `${chip.count} × ${typeLabel || chip.type}`;
        const parts = (chip.breakdown || [])
            .filter((b) => b.status)
            .map((b) => `${b.count} ${b.status}`);
        const without = (chip.breakdown || []).filter((b) => !b.status).reduce((n, b) => n + b.count, 0);
        if (without && parts.length) parts.push(`${without} ohne Status`);
        if (!parts.length) return head;
        return `${head}: ${parts.join(', ')}`;
    }

    function asOfText(asOf) {
        if (!asOf) return '';
        const time = String(asOf).split('T')[1] || String(asOf);
        return `Stand ${time}`;
    }

    function csrf() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    }

    async function api(path, params, options) {
        const query = new URLSearchParams();
        Object.entries(params || {}).forEach(([key, value]) => {
            if (value !== undefined && value !== null && value !== '' && value !== false) {
                query.set(key, value === true ? '1' : String(value));
            }
        });
        const url = query.toString() ? `${path}?${query}` : path;
        const init = Object.assign({ headers: { 'Accept': 'application/json' }, credentials: 'same-origin' }, options || {});
        if (init.method && init.method !== 'GET') {
            init.headers = Object.assign({ 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() }, init.headers);
        }
        const response = await fetch(url, init);
        if (response.status === 401) {
            window.location.href = '/login';
            throw new Error('Nicht angemeldet.');
        }
        let body = null;
        try { body = await response.json(); } catch (err) { body = null; }
        if (!response.ok || !body || body.success === false) {
            throw new Error((body && body.error) || `Fehler ${response.status}`);
        }
        return body;
    }

    root.ViewsCore = { typeStyle, el, clear, swatch, chipLabel, asOfText, api, hash };
}(typeof window !== 'undefined' ? window : globalThis));
