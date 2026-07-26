// Minimaler Markdown-Renderer fuer die Dokumentvorschau.
//
// SICHERHEIT: Der Eingabetext stammt aus fremden Dokumenten. knovas_extract
// entfernt serverseitig Markup, escaped aber keinen Textinhalt -- ein DOCX mit
// dem woertlichen Text "<script>" liefert genau diese Zeichen zurueck.
// Deshalb wird hier IMMER zuerst escaped und erst danach formatiert.
// Diese Reihenfolge umzudrehen oeffnet XSS.
(function () {
    'use strict';

    var ALLOWED_LINK_SCHEMES = /^(https?:|mailto:)/i;
    var LINK_PATTERN = /\[([^\]]+)\]\(((?:[^()\s]|\([^()\s]*\))*)\)/g;

    function escapeHtml(text) {
        return String(text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    /** Inline-Auszeichnung auf bereits escaptem Text. */
    function renderInline(escaped) {
        return escaped
            .replace(/`([^`]+)`/g, '<code>$1</code>')
            .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
            .replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>')
            .replace(LINK_PATTERN, function (match, label, href) {
                // Zweite Verteidigungslinie: der Server filtert Schemata bereits.
                if (!ALLOWED_LINK_SCHEMES.test(href)) {
                    return label;
                }
                return '<a href="' + href + '" target="_blank" rel="noopener noreferrer">' + label + '</a>';
            });
    }

    function render(markdown) {
        var escaped = escapeHtml(markdown == null ? '' : markdown);
        var lines = escaped.split(/\r?\n/);
        var html = [];
        var listOpen = false;

        function closeList() {
            if (listOpen) {
                html.push('</ul>');
                listOpen = false;
            }
        }

        for (var i = 0; i < lines.length; i++) {
            var line = lines[i];
            var heading = /^(#{1,6})\s+(.*)$/.exec(line);
            var bullet = /^\s*[-*]\s+(.*)$/.exec(line);

            if (heading) {
                closeList();
                var level = Math.min(heading[1].length + 2, 6);
                html.push('<h' + level + '>' + renderInline(heading[2]) + '</h' + level + '>');
            } else if (bullet) {
                if (!listOpen) {
                    html.push('<ul>');
                    listOpen = true;
                }
                html.push('<li>' + renderInline(bullet[1]) + '</li>');
            } else if (line.trim() === '') {
                closeList();
            } else {
                closeList();
                html.push('<p>' + renderInline(line) + '</p>');
            }
        }
        closeList();
        return html.join('');
    }

    window.KnovasMarkdown = { render: render, escapeHtml: escapeHtml };
})();
