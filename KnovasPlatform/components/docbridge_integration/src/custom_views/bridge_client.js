// The Knovas bridge of a custom view: the only way a view talks to the page around it.
//
// Runs first in the view's document (inlined by custom_views/document.py with the page's nonce) and defines
// window.knovas, nothing else. The page posts one window message {type: "knovas.port"} carrying a MessagePort;
// the bridge takes it only from window.parent and only once, and from then on ignores window messages. Requests go
// over the port as {id, kind, ...}; answers are {id, ok: true, data} or {id, ok: false, error, status}, matched by
// id. This file must never contain a closing script tag, an HTML comment opener or a web address.
(function () {
  "use strict";

  var parentWindow = window.parent; // taken now: a view's own script runs later and could replace window.parent
  var nextId = 1;
  var pending = new Map(); // id -> {resolve, reject}
  var resolvePort;
  var portArrived = new Promise(function (resolve) {
    resolvePort = resolve;
  });

  function failure(message, status, retryAfter) {
    var error = new Error(typeof message === "string" && message ? message : "Die Anfrage ist fehlgeschlagen.");
    error.status = typeof status === "number" ? status : 0;
    if (typeof retryAfter === "number" && isFinite(retryAfter)) {
      error.retryAfter = retryAfter; // seconds until a refused read may be tried again (a 429's retry_after)
    }
    return error;
  }

  function onAnswer(event) {
    var answer = event.data;
    if (!answer || typeof answer !== "object" || !pending.has(answer.id)) {
      return; // not an answer, or one nobody waits for
    }
    var waiting = pending.get(answer.id);
    pending.delete(answer.id);
    if (answer.ok === true) {
      waiting.resolve(answer.data);
    } else {
      waiting.reject(failure(answer.error, answer.status, answer.retry_after));
    }
  }

  function onWindowMessage(event) {
    if (event.source !== parentWindow) {
      return;
    }
    var data = event.data;
    var ports = event.ports;
    if (!data || data.type !== "knovas.port" || !ports || !ports[0]) {
      return;
    }
    window.removeEventListener("message", onWindowMessage);
    var port = ports[0];
    port.onmessage = onAnswer;
    resolvePort(port);
  }

  window.addEventListener("message", onWindowMessage);

  // Posts once the port arrived; calls made before that wait for it, in order.
  function send(message, onError) {
    portArrived.then(function (port) {
      try {
        port.postMessage(message);
      } catch (error) {
        if (onError) {
          onError(error);
        }
      }
    });
  }

  function request(message) {
    return new Promise(function (resolve, reject) {
      pending.set(message.id, {resolve: resolve, reject: reject});
      send(message, function (error) {
        pending.delete(message.id);
        reject(error);
      });
    });
  }

  var knovas = Object.freeze({
    read: function (query) {
      return request({id: nextId++, kind: "read", query: query});
    },
    open: function (url) {
      if (typeof url !== "string") {
        return Promise.reject(new TypeError("knovas.open erwartet eine Adresse als Text."));
      }
      return request({id: nextId++, kind: "open", url: url}).then(function () {
        return undefined;
      });
    },
    resize: function (height) {
      if (typeof height !== "number" || !isFinite(height)) {
        throw new TypeError("knovas.resize erwartet eine Zahl.");
      }
      send({id: nextId++, kind: "resize", height: height}); // fire-and-forget: an answer finds no waiting id
    },
    ready: function (callback) {
      if (typeof callback !== "function") {
        throw new TypeError("knovas.ready erwartet eine Funktion.");
      }
      portArrived.then(function () {
        callback();
      });
    }
  });

  Object.defineProperty(window, "knovas", {value: knovas, enumerable: true});
})();
