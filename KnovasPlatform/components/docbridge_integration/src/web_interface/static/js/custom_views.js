// Eigene Ansichten: the picker and the bridge host (Task 10; SS-626).
//
// A view runs in a sandboxed srcdoc iframe (scripts only, an opaque origin) cloned from the page's
// <template id="cvFrameTemplate"> for every load and never reused. On the frame's load event the page posts it
// one window message {type: "knovas.port"} carrying port2 of a new MessageChannel (target origin "*": the frame's
// origin is opaque) and from then on listens only on port1 -- there is no window message listener on this page.
// Requests over the port are {id, kind: "read" | "open" | "resize", ...}; answers are {id, ok: true, data} or
// {id, ok: false, error, status} (a 429 also carries retry_after in seconds when the server named it). A message
// of any other shape is ignored and never answered. The protocol's other side is custom_views/bridge_client.js.
//
// SECURITY: every text drawn here (titles, file names, fingerprints, messages) goes through textContent; this
// file never writes markup. A read is relayed to POST /api/custom-views/<id>/read with the CSRF header, one at a
// time per view load; a view opens only https links to the hosts its manifest names.
//
// The checks and the host of one view load are plain functions, exported to node (tests/test_custom_views_page.py)
// through a guarded module.exports; in the browser the page wires them up.
(function () {
  'use strict';

  var MIN_HEIGHT = 200;
  var MAX_HEIGHT = 20000;
  var MAX_URL = 2048;

  var INVALID = 'Diese Anfrage ist ungültig.';
  var BUSY = 'Bitte warten, bis die vorige Abfrage fertig ist.';
  var LINK_REFUSED = 'Dieser Link ist für diese Ansicht nicht freigegeben.';
  var OFFLINE = 'Keine Verbindung zum Server. Bitte erneut versuchen.';

  // ── checks ─────────────────────────────────────────────────────────────

  /** An object literal: what a structured clone of {...} gives, not an array, a Date or a Map. */
  function isPlainObject(value) {
    if (value === null || typeof value !== 'object') {
      return false;
    }
    var proto = Object.getPrototypeOf(value);
    return proto === Object.prototype || proto === null;
  }

  /**
   * What a message from the view is: 'read', 'open' or 'resize' when its shape is exact; 'invalid' for a request
   * (an integer id and a known kind) with a wrong field, answered with a refusal; 'ignore' for anything else,
   * which is never answered.
   */
  function shapeOf(message) {
    if (!isPlainObject(message) || !Number.isSafeInteger(message.id)) {
      return 'ignore';
    }
    var kind = message.kind;
    if (kind === 'read') {
      return isPlainObject(message.query) ? kind : 'invalid';
    }
    if (kind === 'open') {
      return typeof message.url === 'string' && message.url.length <= MAX_URL ? kind : 'invalid';
    }
    if (kind === 'resize') {
      return typeof message.height === 'number' && Number.isFinite(message.height) ? kind : 'invalid';
    }
    return 'ignore';
  }

  /**
   * The address a view may open, or null: https only, no user or password, no port, and a host that is exactly
   * one of the document's links (compared in lower case).
   */
  function allowedLink(url, hosts) {
    if (typeof url !== 'string' || url.length > MAX_URL || !Array.isArray(hosts)) {
      return null;
    }
    var parsed;
    try {
      parsed = new URL(url);
    } catch (_) {
      return null;
    }
    if (parsed.protocol !== 'https:' || parsed.username !== '' || parsed.password !== '' || parsed.port !== '') {
      return null;
    }
    var host = parsed.hostname.toLowerCase();
    for (var i = 0; i < hosts.length; i++) {
      if (typeof hosts[i] === 'string' && hosts[i].toLowerCase() === host) {
        return parsed.href;
      }
    }
    return null;
  }

  /** A height in whole pixels between MIN_HEIGHT and MAX_HEIGHT. */
  function clampHeight(height) {
    return Math.round(Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, height)));
  }

  /** Seconds from a Retry-After header in its seconds form; null for a date, a fraction or nothing. */
  function retryAfterSeconds(header) {
    if (typeof header !== 'string' || !/^\s*\d{1,9}\s*$/.test(header)) {
      return null;
    }
    return parseInt(header, 10);
  }

  /** German text for an answer without a message of its own (a proxy's page, say); never the raw body. */
  function statusText(status) {
    if (status === 400) return INVALID;
    if (status === 401) return 'Bitte melden Sie sich erneut an.';
    if (status === 403) return 'Diese Anfrage ist nicht erlaubt.';
    if (status === 404) return 'Diese Ansicht gibt es nicht.';
    if (status === 429) return 'Zu viele Abfragen. Bitte einen Moment warten.';
    if (status >= 500) return 'Der Server ist gerade nicht erreichbar. Bitte später erneut versuchen.';
    return 'Die Anfrage ist fehlgeschlagen (HTTP ' + status + ').';
  }

  function readJson(response) {
    return Promise.resolve()
      .then(function () {
        return response.json();
      })
      .catch(function () {
        return null;
      });
  }

  // ── the host of one view load ──────────────────────────────────────────

  /**
   * Answers the requests of one view load.
   *
   * @param {string} viewId the view's id (from the page's picker)
   * @param {string[]} links the hosts the document answer named
   * @param {{fetch: Function, csrf: Function, open: Function, resize: Function}} effects the page's side
   * @param {Function} reply posts one answer to the view (port1.postMessage)
   * @returns {{handle: Function, close: Function}} handle(message) returns a promise that settles once the
   *     message is answered; after close() nothing more is sent or answered.
   */
  function createHost(viewId, links, effects, reply) {
    var hosts = Array.isArray(links) ? links.slice() : [];
    var readUrl = '/api/custom-views/' + encodeURIComponent(viewId) + '/read';
    var controller = new AbortController();
    var reading = false;
    var closed = false;

    function answer(message) {
      if (!closed) {
        reply(message);
      }
    }

    function refuse(id, status, error) {
      answer({id: id, ok: false, status: status, error: error});
    }

    function read(id, query) {
      if (reading) {
        refuse(id, 429, BUSY);
        return Promise.resolve();
      }
      var body;
      try {
        body = JSON.stringify(query);
      } catch (_) {
        refuse(id, 400, INVALID);
        return Promise.resolve();
      }
      reading = true;
      var request;
      try {
        request = Promise.resolve(effects.fetch(readUrl, {
          method: 'POST',
          credentials: 'same-origin',
          cache: 'no-store',
          headers: {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'X-CSRF-Token': effects.csrf()
          },
          body: body,
          signal: controller.signal
        }));
      } catch (error) {
        request = Promise.reject(error);
      }
      return request
        .then(function (response) {
          return readJson(response).then(function (data) {
            if (response.ok && isPlainObject(data)) {
              return {id: id, ok: true, data: data};
            }
            var message = data && typeof data.error === 'string' && data.error.trim()
              ? data.error : statusText(response.status);
            var failed = {id: id, ok: false, status: response.status, error: message};
            if (response.status === 429) {
              var seconds = retryAfterSeconds(response.headers.get('Retry-After'));
              if (seconds !== null) {
                failed.retry_after = seconds;
              }
            }
            return failed;
          });
        }, function () {
          return closed ? null : {id: id, ok: false, status: 0, error: OFFLINE};
        })
        .then(function (result) {
          reading = false;
          if (result) {
            answer(result);
          }
        });
    }

    function handle(message) {
      if (closed) {
        return Promise.resolve();
      }
      var kind = shapeOf(message);
      if (kind === 'ignore') {
        return Promise.resolve();
      }
      var id = message.id;
      if (kind === 'invalid') {
        refuse(id, 400, INVALID);
      } else if (kind === 'read') {
        return read(id, message.query);
      } else if (kind === 'open') {
        var href = allowedLink(message.url, hosts);
        if (href === null) {
          refuse(id, 403, LINK_REFUSED);
        } else {
          effects.open(href);
          answer({id: id, ok: true, data: null});
        }
      } else {
        effects.resize(clampHeight(message.height));
        answer({id: id, ok: true, data: null});
      }
      return Promise.resolve();
    }

    function close() {
      closed = true;
      controller.abort();
    }

    return {handle: handle, close: close};
  }

  var exported = {
    MIN_HEIGHT: MIN_HEIGHT,
    MAX_HEIGHT: MAX_HEIGHT,
    MAX_URL: MAX_URL,
    shapeOf: shapeOf,
    allowedLink: allowedLink,
    clampHeight: clampHeight,
    retryAfterSeconds: retryAfterSeconds,
    createHost: createHost
  };

  if (typeof module === 'object' && module !== null && typeof module.exports === 'object') {
    module.exports = exported; // node, for the tests; the browser never has a module
    return;
  }

  // ── the page ───────────────────────────────────────────────────────────

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') || '' : '';
  }

  function redirectToLogin() {
    var next = window.location.pathname + window.location.search;
    window.location.href = '/login?next=' + encodeURIComponent(next);
  }

  /** GET a JSON answer of the page's API; rejects with an Error carrying a German message and the status. */
  function getJson(url, signal) {
    return fetch(url, {
      method: 'GET',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: {'Accept': 'application/json', 'X-CSRF-Token': csrfToken()},
      signal: signal
    }).then(function (response) {
      return readJson(response).then(function (data) {
        if (response.ok && isPlainObject(data)) {
          return data;
        }
        if (response.status === 401) {
          redirectToLogin();
        }
        var error = new Error(data && typeof data.error === 'string' && data.error.trim()
          ? data.error : statusText(response.status));
        error.status = response.status;
        throw error;
      });
    }, function (error) {
      if (error && error.name === 'AbortError') {
        throw error;
      }
      var offline = new Error(OFFLINE);
      offline.status = 0;
      throw offline;
    });
  }

  function element(tag, className, text) {
    var node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined && text !== null) {
      node.textContent = String(text);
    }
    return node;
  }

  function changedLabel(value) {
    var text = typeof value === 'string' ? value : '';
    var date = new Date(text);
    if (!text || isNaN(date.getTime())) {
      return text;
    }
    try {
      return date.toLocaleString('de-CH', {dateStyle: 'medium', timeStyle: 'short'});
    } catch (_) {
      return text;
    }
  }

  /** The administrator's block: each view's files with fingerprint and change time, from the list. */
  function loadFiles() {
    var box = document.getElementById('cvAdminFiles');
    if (!box) {
      return; // the administrator's block is not on the page
    }
    getJson('/api/custom-views', undefined).then(function (answer) {
      var views = Array.isArray(answer.views) ? answer.views : [];
      var listed = views.filter(function (view) {
        return isPlainObject(view) && Array.isArray(view.files);
      });
      box.textContent = '';
      if (!listed.length) {
        box.appendChild(element('p', 'cv-hint', 'Keine Dateien.'));
        return;
      }
      listed.forEach(function (view) {
        var title = isPlainObject(view.title) && typeof view.title.de === 'string' ? view.title.de : '';
        var block = element('div', 'cv-files');
        var heading = element('h3', 'cv-files-title', title || String(view.id));
        if (title) {
          heading.appendChild(document.createTextNode(' '));
          heading.appendChild(element('span', 'cv-mono cv-muted', '(' + String(view.id) + ')'));
        }
        block.appendChild(heading);
        var table = element('table', 'cv-files-table');
        var head = table.createTHead().insertRow();
        ['Datei', 'Fingerabdruck', 'Geändert'].forEach(function (label) {
          var cell = element('th', '', label);
          cell.scope = 'col';
          head.appendChild(cell);
        });
        var body = table.createTBody();
        view.files.forEach(function (file) {
          if (!isPlainObject(file)) {
            return;
          }
          var row = body.insertRow();
          row.appendChild(element('td', 'cv-mono', file.name));
          var sha = typeof file.sha256 === 'string' ? file.sha256 : '';
          var fingerprint = element('td', 'cv-mono', sha.slice(0, 12));
          fingerprint.title = sha;
          row.appendChild(fingerprint);
          var changed = element('td', '');
          var time = element('time', '', changedLabel(file.changed_at));
          if (typeof file.changed_at === 'string') {
            time.dateTime = file.changed_at;
            time.title = file.changed_at;
          }
          changed.appendChild(time);
          row.appendChild(changed);
        });
        var scroller = element('div', 'cv-files-scroll');
        scroller.appendChild(table);
        block.appendChild(scroller);
        box.appendChild(block);
      });
    }).catch(function (error) {
      box.textContent = '';
      box.appendChild(element('p', 'cv-error',
        'Die Dateiliste konnte nicht geladen werden. ' + (error && error.message ? error.message : '')));
    });
  }

  /** The picker, the bar and the frame of the view on screen. */
  function startViews() {
    var page = document.getElementById('cvPage');
    var picker = document.getElementById('cvPicker');
    var frameHost = document.getElementById('cvFrameHost');
    var frameTemplate = document.getElementById('cvFrameTemplate');
    var barTitle = document.getElementById('cvBarTitle');
    var messageBox = document.getElementById('cvMessage');
    var loading = document.getElementById('cvLoading');
    if (!page || !picker || !frameHost || !frameTemplate || !barTitle || !messageBox) {
      return; // no view to show (see the template's empty state)
    }
    var picks = Array.prototype.slice.call(picker.querySelectorAll('a.cv-pick[data-view-id]'));
    if (!picks.length) {
      return; // an administrator's page while no view is valid for them: nothing to frame
    }
    var current = null; // the view load on screen: {viewId, controller, frame, host, port}

    function pickFor(viewId) {
      for (var i = 0; i < picks.length; i++) {
        if (picks[i].getAttribute('data-view-id') === viewId) {
          return picks[i];
        }
      }
      return null;
    }

    function viewFromPath() {
      var match = /^\/custom-views\/([a-z0-9-]+)\/?$/.exec(window.location.pathname);
      var pick = match ? pickFor(match[1]) : null;
      return (pick || picks[0]).getAttribute('data-view-id');
    }

    function showMessage(text) {
      messageBox.textContent = text;
      messageBox.hidden = !text;
    }

    function setLoading(on) {
      frameHost.setAttribute('aria-busy', on ? 'true' : 'false');
      if (loading) {
        loading.hidden = !on;
      }
    }

    function teardown() {
      if (current === null) {
        return;
      }
      current.controller.abort();
      if (current.host) {
        current.host.close();
      }
      if (current.port) {
        current.port.onmessage = null;
        current.port.close();
      }
      if (current.frame && current.frame.parentNode) {
        current.frame.parentNode.removeChild(current.frame);
      }
      current = null;
    }

    function mark(viewId) {
      picks.forEach(function (pick) {
        if (pick.getAttribute('data-view-id') === viewId) {
          pick.setAttribute('aria-current', 'page');
        } else {
          pick.removeAttribute('aria-current');
        }
      });
      var pick = pickFor(viewId);
      barTitle.textContent = pick ? pick.textContent : '';
    }

    function frameFor(session, documentAnswer) {
      var frame = document.importNode(frameTemplate.content.querySelector('iframe'), true);
      frame.title = 'Eigene Ansicht: ' + barTitle.textContent;
      frame.addEventListener('load', function () {
        if (current !== session) {
          return;
        }
        var channel = new MessageChannel();
        var host = createHost(session.viewId, documentAnswer.links, {
          fetch: function (url, init) {
            return window.fetch(url, init);
          },
          csrf: csrfToken,
          open: function (href) {
            window.open(href, '_blank', 'noopener,noreferrer');
          },
          resize: function (pixels) {
            frame.style.height = pixels + 'px';
          }
        }, function (message) {
          channel.port1.postMessage(message);
        });
        session.host = host;
        session.port = channel.port1;
        channel.port1.onmessage = function (event) {
          host.handle(event.data);
        };
        frame.contentWindow.postMessage({type: 'knovas.port'}, '*', [channel.port2]);
      }, {once: true});
      frame.srcdoc = documentAnswer.html;
      return frame;
    }

    function load(viewId) {
      teardown();
      showMessage('');
      mark(viewId);
      setLoading(true);
      var session = {viewId: viewId, controller: new AbortController(), frame: null, host: null, port: null};
      current = session;
      getJson('/api/custom-views/' + encodeURIComponent(viewId) + '/document', session.controller.signal)
        .then(function (answer) {
          if (current !== session) {
            return;
          }
          if (typeof answer.html !== 'string') {
            throw new Error('Die Ansicht hat kein gültiges Dokument geliefert.');
          }
          var links = Array.isArray(answer.links) ? answer.links.filter(function (host) {
            return typeof host === 'string';
          }) : [];
          session.frame = frameFor(session, {html: answer.html, links: links});
          setLoading(false);
          frameHost.appendChild(session.frame);
        })
        .catch(function (error) {
          if (current !== session || (error && error.name === 'AbortError')) {
            return;
          }
          setLoading(false);
          showMessage('Die Ansicht konnte nicht geladen werden. ' + (error && error.message ? error.message : ''));
        });
    }

    picker.addEventListener('click', function (event) {
      var pick = event.target && event.target.closest ? event.target.closest('a.cv-pick[data-view-id]') : null;
      if (!pick || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
        return; // a new tab or window opens the view's own address
      }
      event.preventDefault();
      var viewId = pick.getAttribute('data-view-id');
      if (!current || current.viewId !== viewId) {
        window.history.pushState({view: viewId}, '', pick.getAttribute('href'));
      }
      load(viewId); // the current view again: a fresh frame
    });

    window.addEventListener('popstate', function (event) {
      var state = event.state;
      var viewId = state && typeof state.view === 'string' && pickFor(state.view) ? state.view : viewFromPath();
      load(viewId);
    });

    var first = page.getAttribute('data-view') && pickFor(page.getAttribute('data-view'))
      ? page.getAttribute('data-view') : viewFromPath();
    window.history.replaceState({view: first}, '', window.location.href);
    load(first);
  }

  function start() {
    loadFiles();
    startViews();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
