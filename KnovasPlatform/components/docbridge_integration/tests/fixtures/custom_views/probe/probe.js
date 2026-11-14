// The probe view: runs the browser probes of the custom views feature and writes each result into its own page as
// text, "PASS <name>" or "FAIL <name>", then "PROBE DONE n/m". It follows the rules it tests: its output is built
// with textContent. The only innerHTML use is the deliberate attack in the "markup string" probe.
//
// One probe is not judged here: the last button makes the view navigate itself to an outside address. The page's
// policy blocks that by design, and a browser may then replace this whole document with its own "blocked" page, so
// its outcome is read from the Platform page (operator's page, section 10), never from these lines. The button is
// enabled only after "PROBE DONE".
(function () {
  "use strict";

  var list = document.getElementById("probe-results");
  var done = 0;
  var total = 0;
  var finished = false;
  var summaryLine = null;

  function line(text, cls) {
    var item = document.createElement("li");
    item.textContent = text;
    item.className = cls;
    list.appendChild(item);
    return item;
  }

  function summary() {
    if (!finished) {
      return;
    }
    var text = "PROBE DONE " + done + "/" + total;
    if (summaryLine === null) {
      summaryLine = line(text, "probe-pending");
      document.getElementById("probe-leave").disabled = false; // the navigation probe only after every other line
    } else {
      summaryLine.textContent = text;
    }
    window.knovas.resize(document.body.scrollHeight + 40); // every line visible without scrolling the frame
  }

  function record(name, ok, note) {
    line((ok ? "PASS " : "FAIL ") + name + (note ? " (" + note + ")" : ""), ok ? "probe-pass" : "probe-fail");
    done += 1;
    summary();
  }

  function rejects(name, make) {
    return new Promise(function (resolve) {
      var promise;
      try {
        promise = make();
      } catch (error) {
        record(name, true);
        resolve();
        return;
      }
      promise.then(function () {
        record(name, false, "resolved");
        resolve();
      }, function () {
        record(name, true);
        resolve();
      });
    });
  }

  function wait(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  var QUERY = {type: "FeaturePlan", facts: ["status"], limit: 1};

  var probes = [
    function () {
      var name = "read returns nodes of the one type";
      return window.knovas.read(QUERY).then(function (answer) {
        record(name, Array.isArray(answer.nodes));
      }, function (error) {
        record(name, false, String(error && error.message));
      });
    },
    function () {
      var value;
      try { value = document.cookie; } catch (error) { value = ""; }
      record("document.cookie is empty", value === "");
    },
    function () {
      return rejects("fetch of the Platform API rejects", function () { return fetch("/api/custom-views"); });
    },
    function () {
      return rejects("fetch of an outside address rejects", function () { return fetch("https://example.com"); });
    },
    function () {
      var name = "image from an outside address errors";
      return new Promise(function (resolve) {
        var image = new Image();
        var timer = setTimeout(function () {
          record(name, false, "no event");
          resolve();
        }, 3000);
        image.onerror = function () {
          clearTimeout(timer);
          record(name, true, "check that the server log shows no request");
          resolve();
        };
        image.onload = function () {
          clearTimeout(timer);
          record(name, false, "loaded");
          resolve();
        };
        image.src = "https://example.com/x.png";
      });
    },
    function () {
      var opened;
      try { opened = window.open("https://example.com/", "_blank"); } catch (error) { opened = null; }
      record("window.open returns null", opened === null);
    },
    function () {
      // What the sandbox does (no allow-forms): the submission is dropped, so this document stays. A post that went
      // out would have navigated the frame and ended this script before the line below.
      var name = "form post is blocked";
      var marker = document.getElementById("probe-target");
      var form = document.createElement("form");
      form.method = "post";
      form.action = "https://example.com/post";
      document.body.appendChild(form);
      var how = "submit did nothing";
      try { form.submit(); } catch (error) { how = "submit threw " + error.name; }
      return wait(1500).then(function () {
        record(name, document.body.contains(marker),
          how + "; also check that the network log shows no request to example.com");
      });
    },
    function () {
      var name = "markup string with script and style does not run or apply";
      var target = document.getElementById("probe-target");
      var holder = document.createElement("div");
      document.body.appendChild(holder);
      window.probeScriptRan = false;
      var refused = false;
      try {
        // Deliberate attack: this is the one place a string is parsed as markup.
        holder.innerHTML = "<script>window.probeScriptRan = true<\/script>" +
          "<style>.probe-target { display: none }<\/style>";
      } catch (error) {
        refused = true; // Trusted Types refuse the string
      }
      var applied = window.getComputedStyle(target).display === "none";
      record(name, window.probeScriptRan === false && !applied, refused ? "refused by Trusted Types" : "inert");
    },
    function () {
      var name = "a self-posted port is ignored";
      var channel = new MessageChannel();
      window.postMessage({type: "knovas.port"}, "*", [channel.port2]);
      return window.knovas.read(QUERY).then(function (answer) {
        record(name, Array.isArray(answer.nodes));
      }, function () {
        record(name, false, "bridge broke");
      });
    },
    function () {
      return rejects("open of a host the manifest does not name is refused", function () {
        return window.knovas.open("https://not-allowed.example.net/");
      });
    },
    function () {
      var name = "link with target _top leaves the page and the frame unchanged";
      var marker = document.getElementById("probe-target");
      var link = document.createElement("a");
      link.setAttribute("href", "https://example.com/");
      link.setAttribute("target", "_top");
      link.textContent = "x";
      document.body.appendChild(link);
      link.click();
      return wait(1500).then(function () {
        record(name, document.body.contains(marker), "also check that the page tab did not move");
      });
    }
  ];

  // Not counted, judged from the Platform page: the view navigates itself. Expected: blocked (the frame shows the
  // browser's blocked page; the Platform page and other views are unaffected).
  document.getElementById("probe-leave").addEventListener("click", function () {
    line("NAVIGATION PROBE started: judge it from the page", "probe-pending");
    try {
      window.location.href = "https://example.com/";
    } catch (error) {
      line("NAVIGATION PROBE: the location change threw " + error.name, "probe-pending");
    }
  }, {once: true});

  // The allowed link opens a tab only after a click (a popup blocker refuses the rest).
  var openName = "open of an allowed host opens a tab (click the button)";
  document.getElementById("probe-open").addEventListener("click", function () {
    window.knovas.open("https://example.com/").then(function () {
      record(openName, true, "check that a tab opened");
    }, function (error) {
      record(openName, false, String(error && error.message));
    });
  }, {once: true});

  total = probes.length + 1;
  window.knovas.ready(function () {
    var chain = Promise.resolve();
    probes.forEach(function (probe) {
      chain = chain.then(probe);
    });
    chain.then(function () {
      finished = true;
      summary();
    });
    window.knovas.resize(document.body.scrollHeight + 80);
  });
}());
