#!/usr/bin/env bash
# Give the fixture files old modification times (git does not keep mtimes). Run before loading the fixture.
set -euo pipefail
cd "$(dirname "$0")/share/Quarzfels"
while IFS='|' read -r when path; do
  touch -m -d "$when" "$path"
done <<'EOF'
2016-03-08 09:12:00|Gemeinsam/Vorlagen/Vollmacht Mandat.txt
2017-11-21 16:40:00|Gemeinsam/Vorlagen/Honorarvereinbarung_Entwurf.txt
2022-01-31 18:05:00|Gemeinsam/Personal/Lohnliste 2021 – vertraulich.txt
2022-04-08 09:30:00|Gemeinsam/Personal/Zürcher Simon/Arbeitszeugnis Entwurf.txt
2022-03-01 08:15:00|Scan Eingang/SCN_20220301_0815.txt
2019-06-03 11:27:00|Mandate/2019-0042 Bäckerei Zürcher AG/Klageschrift Entwurf.txt
2019-05-14 08:55:00|Mandate/2019-0042 Bäckerei Zürcher AG/Korrespondenz/Schreiben Gegenpartei 2019-05-14.txt
2021-03-02 14:10:00|Mandate/2021-0107 Müller Söhne GmbH/Mietvertrag Prüfung.txt
2022-02-28 17:30:00|Partner/Gewinnverteilung 2021.txt
2020-09-17 07:45:00|Users/mbaer/Notizen Übersicht.txt
2021-02-19 12:00:00|Users/aroth/Pendenzen.txt
2021-08-05 15:22:00|Users/lmueller/Recherche Mietrecht.txt
2018-04-12 10:03:00|Users/nkeller/Telefonliste.txt
2022-03-31 16:59:00|Users/szuercher/Übergabe.txt
EOF
echo "fixture mtimes set ($(find . -type f | wc -l) files)"
