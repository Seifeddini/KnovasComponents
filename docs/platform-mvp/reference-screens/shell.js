// Injects the sidebar. It shows only the area the person is in.
// <body data-area="work|admin|builder" data-on="<active entry>">
(function () {
  var b = document.body, area = b.getAttribute("data-area"), on = b.getAttribute("data-on");
  function item(n) { return '<span class="nav' + (n === on ? " on" : "") + '">' + n + "</span>"; }
  var h = '<img src="assets/knovas-logo.svg" alt="Knovas">';
  if (area === "work") {
    // Members see a question button and their own chats. Nothing else.
    h += '<span class="new">+ Neue Frage</span>';
    h += '<div class="grp">Heute</div>' + ["Kündigung Mietvertrag Seefeldstrasse", "Vollmacht Hardturm-Areal"].map(item).join("");
    h += '<div class="grp">Diese Woche</div>' + ["Fristen Baurechtsvertrag Oerlikon", "Muster Geheimhaltungsvereinbarung", "Zuständigkeit Schlichtungsbehörde"].map(item).join("");
    // Only administrators see the two links to the other areas.
    h += '<div class="foot"><span class="nav">Verwaltung</span><span class="nav">Builder</span>';
  } else {
    var t = area === "admin" ? ["Verwaltung", ["Personen", "Gruppen", "Zugriff", "Protokoll"]] : ["Builder", ["Quellen", "Modell", "Freigaben"]];
    h += '<span class="nav">← Arbeitsbereich</span><div class="grp">' + t[0] + "</div>" + t[1].map(item).join("") + '<div class="foot">';
  }
  h += '<div class="who"><span class="av">LB</span><span>Lea Brunner</span></div></div>';
  var s = document.createElement("aside");
  s.className = "side";
  s.innerHTML = h;
  b.insertBefore(s, b.firstChild);
})();
