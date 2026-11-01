/* Brain Builder -- review comments (build plan step B11).
 *
 * A comment belongs to one element of the workspace, named as the change log
 * names it ("ontology.types/Invoice"): a change-set item on the review page,
 * or the type or relation open on the canvas. It stays open until someone
 * resolves it; "seither geändert" says the element changed after it was
 * written, which is usually what addressing it means.
 *
 *   BuilderComments.mount(container, target)  draws a thread and its form
 *   <details data-comment-target="…">           mounts itself when opened
 *
 * The URL comes from <meta name="builder-comments-url"> (the frame sets it
 * when a workspace is chosen). The server decides who may comment or resolve.
 */
(function () {
    'use strict';

    var meta = document.querySelector('meta[name="builder-comments-url"]');
    if (!meta) return;
    var url = meta.content;
    var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';

    function el(tagName, className, text) {
        var node = document.createElement(tagName);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = text;
        return node;
    }
    function when(iso) {
        if (!iso) return '';
        var d = new Date(iso);
        if (isNaN(d.getTime())) return iso;
        function two(n) { return (n < 10 ? '0' : '') + n; }
        return two(d.getDate()) + '.' + two(d.getMonth() + 1) + '.' + d.getFullYear() + ', ' + two(d.getHours()) + ':' + two(d.getMinutes());
    }
    function call(method, address, body) {
        return fetch(address, {
            method: method, credentials: 'same-origin',
            headers: { 'X-CSRF-Token': csrf, 'Accept': 'application/json', 'Content-Type': 'application/json' },
            body: body ? JSON.stringify(body) : undefined,
        }).then(function (r) {
            return r.json().catch(function () { return { success: false, error: 'Der Server hat nicht verständlich geantwortet.' }; });
        }, function () { return { success: false, error: 'Der Server ist nicht erreichbar.' }; });
    }

    function draw(box, target, payload) {
        box.textContent = '';
        var comments = payload.comments || [];
        var may = payload.may || {};
        var list = el('ol', 'b-cm-list');
        comments.forEach(function (c) {
            var li = el('li', 'b-cm' + (c.resolved_at ? ' is-resolved' : ''));
            li.dataset.commentId = c.id;
            var head = el('div', 'b-cm-head');
            head.appendChild(el('strong', null, c.author_name || '–'));
            head.appendChild(el('span', 'b-muted b-small', when(c.at)));
            if (c.resolved_at) head.appendChild(el('span', 'b-tag b-tag--good', 'erledigt' + (c.resolved_by_name ? ' von ' + c.resolved_by_name : '')));
            else if (c.changed_since) head.appendChild(el('span', 'b-tag b-tag--accent', 'seither geändert'));
            li.appendChild(head);
            li.appendChild(el('p', 'b-cm-body', c.body));
            if (!c.resolved_at && may.resolve) {
                var done = el('button', 'b-button b-button--quiet b-button--small', 'Erledigt');
                done.type = 'button';
                done.addEventListener('click', function () {
                    done.disabled = true;
                    call('POST', url + '/' + encodeURIComponent(c.id) + '/resolve').then(function (r) {
                        if (r.success) { r.may = may; draw(box, target, r); changed(box, r.comments); }
                        else { done.disabled = false; error(box, r.error); }
                    });
                });
                li.appendChild(done);
            }
            list.appendChild(li);
        });
        if (!comments.length) box.appendChild(el('p', 'b-muted b-small', 'Noch kein Kommentar.'));
        box.appendChild(list);
        if (may.comment) {
            var form = el('form', 'b-cm-form');
            form.noValidate = true;
            var label = el('label', 'b-visually-hidden', 'Kommentar zu ' + target);
            var text = el('textarea', 'b-textarea');
            text.rows = 2;
            text.id = 'b-cm-' + Math.random().toString(36).slice(2);
            label.htmlFor = text.id;
            text.placeholder = 'Kommentar schreiben …';
            var send = el('button', 'b-button b-button--secondary b-button--small', 'Kommentieren');
            send.type = 'submit';
            form.appendChild(label);
            form.appendChild(text);
            form.appendChild(send);
            form.addEventListener('submit', function (event) {
                event.preventDefault();
                if (!text.value.trim()) return;
                send.disabled = true;
                call('POST', url, { target: target, body: text.value }).then(function (r) {
                    send.disabled = false;
                    if (r.success) { r.may = may; draw(box, target, r); changed(box, r.comments); }
                    else error(box, r.error);
                });
            });
            box.appendChild(form);
        }
    }
    function error(box, message) {
        var old = box.querySelector('.b-cm-error');
        if (old) old.remove();
        box.appendChild(el('p', 'b-small b-error b-cm-error', message || 'Das hat nicht geklappt.'));
    }
    /* The count in a disclosure's summary follows the thread. */
    function changed(box, comments) {
        var holder = box.closest('[data-comment-target]');
        var count = holder && holder.querySelector('[data-comment-count]');
        if (!count) return;
        var open = (comments || []).filter(function (c) { return !c.resolved_at; }).length;
        count.textContent = open ? open + ' offen' : (comments.length ? 'erledigt' : '');
        count.className = 'b-tag ' + (open ? 'b-tag--warning' : 'b-tag--good');
        count.hidden = !comments.length;
    }

    function mount(container, target) {
        var box = el('section', 'b-cm-thread');
        box.setAttribute('aria-label', 'Kommentare');
        box.dataset.commentThread = target;
        var head = el('h3', 'b-h3 b-cm-title', 'Kommentare');
        container.appendChild(head);
        container.appendChild(box);
        box.appendChild(el('p', 'b-muted b-small', 'Lädt …'));
        call('GET', url + '?target=' + encodeURIComponent(target)).then(function (r) {
            if (r.success) draw(box, target, r);
            else { box.textContent = ''; error(box, r.error); }
        });
        return box;
    }

    Array.prototype.forEach.call(document.querySelectorAll('details[data-comment-target]'), function (details) {
        details.addEventListener('toggle', function () {
            if (!details.open || details.dataset.mounted) return;
            details.dataset.mounted = 'true';
            var body = details.querySelector('[data-comment-body]') || details;
            var box = el('section', 'b-cm-thread');
            box.dataset.commentThread = details.dataset.commentTarget;
            body.appendChild(box);
            call('GET', url + '?target=' + encodeURIComponent(details.dataset.commentTarget)).then(function (r) {
                if (r.success) draw(box, details.dataset.commentTarget, r);
                else error(box, r.error);
            });
        });
    });

    window.BuilderComments = { mount: mount };
})();
