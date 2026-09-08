/* Folder picker for the Ingestion profile: expand loads one level of
 * directories from /admin/ingestion/folders; Hinzufuegen writes the path
 * into the profile table. Paths come from RemoteController; nothing here
 * is parsed as HTML.
 */
(function () {
    'use strict';

    var tree = document.getElementById('folder-tree');
    if (!tree) { return; }

    var endpoint = tree.getAttribute('data-endpoint');
    var maxFolders = parseInt(tree.getAttribute('data-max-folders') || '12', 10);
    var statusEl = document.getElementById('folder-tree-status');
    var rows = document.getElementById('folder-rows');
    var emptyEl = document.getElementById('folder-empty');
    var rowTemplate = document.getElementById('folder-row-template');

    function basename(path) {
        var parts = String(path || '').replace(/\\/g, '/').replace(/\/+$/, '').split('/');
        return parts[parts.length - 1] || path;
    }

    function chosenPaths() {
        var found = [];
        if (!rows) { return found; }
        rows.querySelectorAll('input.folder-path').forEach(function (inp) {
            var value = String(inp.value || '').trim();
            if (value) { found.push(value); }
        });
        return found;
    }

    function nextIndex() {
        var used = {};
        if (!rows) { return -1; }
        rows.querySelectorAll('input.folder-path').forEach(function (inp) {
            var match = String(inp.name || '').match(/^folder-(\d+)-path$/);
            if (match) { used[match[1]] = true; }
        });
        for (var i = 0; i < maxFolders; i += 1) {
            if (!used[String(i)]) { return i; }
        }
        return -1;
    }

    function syncEmpty() {
        if (!emptyEl) { return; }
        emptyEl.hidden = chosenPaths().length > 0;
    }

    function setStatus(text) {
        if (!statusEl) { return; }
        if (!text) {
            statusEl.hidden = true;
            statusEl.textContent = '';
            return;
        }
        statusEl.hidden = false;
        statusEl.textContent = text;
    }

    function loadFolders(root) {
        var url = new URL(endpoint, window.location.origin);
        if (root) { url.searchParams.set('root', root); }
        return fetch(url.toString(), {
            credentials: 'same-origin',
            headers: { 'Accept': 'application/json' }
        }).then(function (response) {
            return response.json().then(function (body) {
                return { ok: response.ok, body: body || {} };
            }).catch(function () {
                return { ok: false, body: { error: 'Ordnerliste konnte nicht gelesen werden.', folders: [] } };
            });
        });
    }

    function addFolder(path) {
        if (!path || !rows || !rowTemplate) { return; }
        if (chosenPaths().indexOf(path) !== -1) { return; }
        var index = nextIndex();
        if (index < 0) {
            setStatus('Höchstens ' + maxFolders + ' Ordner auf einem Profil.');
            return;
        }
        var fragment = document.importNode(rowTemplate.content, true);
        var row = fragment.querySelector('tr');
        if (!row) { return; }
        row.querySelectorAll('[name]').forEach(function (el) {
            el.setAttribute(
                'name',
                String(el.getAttribute('name') || '').replace(/__n__/g, String(index))
            );
        });
        var pathInput = row.querySelector('input.folder-path');
        if (pathInput) { pathInput.value = path; }
        rows.appendChild(row);
        syncEmpty();
        refreshAddButtons();
    }

    function refreshAddButtons() {
        var taken = chosenPaths();
        var full = nextIndex() < 0;
        tree.querySelectorAll('.folder-add').forEach(function (button) {
            var path = button.getAttribute('data-path') || '';
            var already = taken.indexOf(path) !== -1;
            button.disabled = full || already;
            button.textContent = already ? 'Auf dem Profil' : 'Hinzufügen';
        });
    }

    function listEl() {
        var ul = document.createElement('ul');
        ul.className = 'folder-tree-list';
        return ul;
    }

    function renderNode(folder) {
        var li = document.createElement('li');
        var row = document.createElement('div');
        row.className = 'folder-node';

        var toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'folder-toggle';
        toggle.setAttribute('aria-expanded', 'false');
        toggle.setAttribute('aria-label', 'Unterordner von ' + folder.name + ' zeigen');
        toggle.textContent = '▸';

        var name = document.createElement('span');
        name.className = 'folder-name';
        name.textContent = folder.name;

        var add = document.createElement('button');
        add.type = 'button';
        add.className = 'folder-add';
        add.setAttribute('data-path', folder.path);
        add.textContent = 'Hinzufügen';
        add.addEventListener('click', function () { addFolder(folder.path); });

        var children = document.createElement('ul');
        children.className = 'folder-tree-list';
        children.hidden = true;

        var loaded = false;
        toggle.addEventListener('click', function () {
            var open = toggle.getAttribute('aria-expanded') === 'true';
            if (open) {
                toggle.setAttribute('aria-expanded', 'false');
                toggle.textContent = '▸';
                children.hidden = true;
                return;
            }
            toggle.setAttribute('aria-expanded', 'true');
            toggle.textContent = '▾';
            children.hidden = false;
            if (loaded) { return; }
            loaded = true;
            var waiting = document.createElement('li');
            waiting.textContent = 'Wird geladen …';
            children.appendChild(waiting);
            loadFolders(folder.path).then(function (result) {
                children.textContent = '';
                var body = result.body;
                if (!result.ok) {
                    var err = document.createElement('li');
                    err.textContent = String(body.error || 'Ordner konnten nicht geladen werden.');
                    children.appendChild(err);
                    return;
                }
                var folders = body.folders || [];
                if (!folders.length) {
                    var empty = document.createElement('li');
                    empty.className = 'hint';
                    empty.textContent = 'Keine Unterordner.';
                    children.appendChild(empty);
                    return;
                }
                folders.forEach(function (child) {
                    children.appendChild(renderNode(child));
                });
                refreshAddButtons();
            }).catch(function () {
                children.textContent = '';
                var fail = document.createElement('li');
                fail.textContent = 'Ordner konnten nicht geladen werden.';
                children.appendChild(fail);
            });
        });

        row.appendChild(toggle);
        row.appendChild(name);
        row.appendChild(add);
        li.appendChild(row);
        li.appendChild(children);
        return li;
    }

    if (rows) {
        rows.addEventListener('click', function (event) {
            var button = event.target.closest ? event.target.closest('.folder-remove') : null;
            if (!button || !rows.contains(button)) { return; }
            var row = button.closest('tr');
            if (row) { row.remove(); }
            syncEmpty();
            refreshAddButtons();
        });
    }

    var topList = listEl();
    tree.insertBefore(topList, statusEl);

    loadFolders(null).then(function (result) {
        var body = result.body;
        if (!result.ok) {
            setStatus(String(body.error || 'RemoteController ist nicht erreichbar.'));
            return;
        }
        var rootPath = String(body.root || '');
        if (!rootPath) {
            setStatus('Kein überwachter Ordner konfiguriert.');
            return;
        }
        setStatus('');
        var rootNode = renderNode({ name: basename(rootPath), path: rootPath });
        topList.appendChild(rootNode);
        var childList = rootNode.querySelector('ul');
        var toggle = rootNode.querySelector('.folder-toggle');
        var folders = body.folders || [];
        if (childList && toggle) {
            toggle.setAttribute('aria-expanded', 'true');
            toggle.textContent = '▾';
            childList.hidden = false;
            if (!folders.length) {
                var empty = document.createElement('li');
                empty.className = 'hint';
                empty.textContent = 'Keine Unterordner.';
                childList.appendChild(empty);
            } else {
                folders.forEach(function (child) {
                    childList.appendChild(renderNode(child));
                });
            }
            // Expanding the root again must not refetch what we just drew.
            toggle.replaceWith(toggle.cloneNode(true));
            var frozen = rootNode.querySelector('.folder-toggle');
            frozen.addEventListener('click', function () {
                var open = frozen.getAttribute('aria-expanded') === 'true';
                frozen.setAttribute('aria-expanded', open ? 'false' : 'true');
                frozen.textContent = open ? '▸' : '▾';
                childList.hidden = open;
            });
        }
        refreshAddButtons();
    }).catch(function () {
        setStatus('RemoteController ist nicht erreichbar.');
    });

    syncEmpty();
})();
