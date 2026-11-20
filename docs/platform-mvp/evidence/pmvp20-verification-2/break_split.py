"""Throwaway: try to make an unreferenced statement reach reply.sentences.

Each case is (label, answer). The unreferenced statement always contains the
marker word GEHEIM (or a distinctive word given as 3rd element); a case is a
JOIN when any shown sentence contains it.
"""
import sys

sys.path.insert(0, sys.argv[1])  # P/src

from assistant.service import AssistantService  # noqa: E402

ROWS = [
    {"doc_id": f"Akten/doc-{n}.pdf", "path": f"Akten/doc-{n}.pdf", "title": f"Dokument {n}",
     "page_number": 1, "snippet": f"Text {n}."}
    for n in (1, 2, 3)
]


def reply_for(answer):
    def search(q, limit):
        return [dict(r) for r in ROWS]

    def generate(*, system_instruction, messages, max_tokens):
        return answer

    return AssistantService(search=search, generate=generate).answer("Welche Frist gilt?")


R = " Die Frist betraegt drei Monate [1]."
CASES = [
    # amounts / numbers / units
    ("amount Mio.", "Der Streitwert betraegt CHF 2 GEHEIM Mio." + R),
    ("amount Mrd.", "Der Schaden betraegt GEHEIM 1,2 Mrd." + R),
    ("Tsd.", "Die Busse betraegt GEHEIM 5 Tsd." + R),
    ("Fr. (Franken) final", "Die Miete betraegt GEHEIM 500 Fr." + R),
    ("MwSt. final", "Die Miete betraegt CHF 500 GEHEIM inkl. MwSt." + R),
    ("zzgl. MwSt. final", "Die Miete betraegt CHF 500 GEHEIM zzgl. MwSt." + R),
    ("preposition auf 12.", "Die Zahl der Mieter GEHEIM stieg auf 12." + R),
    ("preposition von 50 auf 80.", "Die Kosten GEHEIM stiegen von 50 auf 80." + R),
    ("preposition um 10.", "Die Miete GEHEIM erhoehte sich um 10." + R),
    ("bis zu 20.", "Die Busse GEHEIM betraegt bis zu 20." + R),
    ("am 12. (order example)", "Die Miete ist GEHEIM faellig am 12." + R),
    ("seit 2019 (year)", "Der Mieter wohnt GEHEIM dort seit 2019." + R),
    ("year 2023.", "Der Vertrag stammt GEHEIM aus 2023." + R),
    ("date 1.1.2024.", "Der Vertrag gilt GEHEIM seit 1.1.2024." + R),
    ("Abs. 2.", "Massgebend GEHEIM ist Art. 5 Abs. 2." + R),
    ("Art. 5.", "Massgebend GEHEIM ist Art. 5." + R),
    ("§ 5.", "Massgebend GEHEIM ist § 5." + R),
    ("Ziff. 3.2.", "Massgebend GEHEIM ist Ziff. 3.2." + R),
    ("lit. a. final", "Die Kuendigung GEHEIM ist anfechtbar nach Art. 271a Abs. 1 lit. a." + R),
    ("lit. b. then year", "Es gilt GEHEIM Ziff. 2 lit. b. 2023 wurde dies geaendert [1]."),
    ("f. (folgende) final", "Dies regeln GEHEIM Art. 271 f." + R),
    ("ff. final", "Dies regeln GEHEIM Art. 271 ff." + R),
    ("Roman III final", "Massgebend GEHEIM ist Kapitel III." + R),
    ("Roman I then year", "Massgebend GEHEIM ist Anhang I. 2023 wurde die Frist verlaengert [1]."),
    ("party B. then year", "Vermieterin GEHEIM ist B. 2023 kuendigte sie den Vertrag [1]."),
    ("initial", "Der Vertrag wurde GEHEIM von Hans M. unterzeichnet." + R),
    ("initial mid", "Hans M. Mueller GEHEIM unterzeichnete." + R),
    ("BGE", "So entschied GEHEIM das Gericht in BGE 140 III 115." + R),
    ("S. 3.", "Siehe GEHEIM S. 3." + R),
    ("Nr. 4.", "Siehe GEHEIM Nr. 4." + R),
    # abbreviations that end sentences
    ("& Co.", "Vertragspartei GEHEIM ist die Mueller & Co." + R),
    ("et al.", "So GEHEIM auch Mueller et al." + R),
    ("u. a. final", "Dies betrifft GEHEIM Mietvertraege u. a." + R),
    ("u.a. final", "Dies betrifft GEHEIM Mietvertraege u.a." + R),
    ("u.ae. final", "Dies betrifft GEHEIM Mietvertraege u.ä." + R),
    ("o.ae. final", "Dies betrifft GEHEIM Kuendigungen o.ä." + R),
    ("u. dgl. final", "Dies betrifft GEHEIM Mietvertraege u. dgl." + R),
    ("a.a.O. final", "So GEHEIM Mueller, a.a.O." + R),
    ("ebd. final", "So GEHEIM auch ebd." + R),
    ("e.V. final", "Klaegerin GEHEIM ist der Mieterverband e.V." + R),
    ("Jh. final", "Die Regel GEHEIM stammt aus dem 19. Jh." + R),
    ("Jan. final", "Der Vertrag GEHEIM datiert vom 5. Jan." + R),
    ("St. final?", "Der Sitz GEHEIM ist in Zuerich, Kt." + R),
    ("jr. final", "Mieter GEHEIM ist Hans Mueller jr." + R),
    ("usw. final", "Dies betrifft GEHEIM Mietvertraege usw." + R),
    ("etc. final", "Dies betrifft GEHEIM Mietvertraege etc." + R),
    ("bzw. final", "Das gilt GEHEIM bzw." + R),
    ("vgl. final", "So GEHEIM vgl." + R),
    ("d.h. final", "Das GEHEIM d.h." + R),
    ("z.B. final", "Mietvertraege GEHEIM z.B." + R),
    ("ca. final", "Die Frist dauert GEHEIM ca." + R),
    ("max. final", "Die Frist dauert GEHEIM 3 Monate max." + R),
    ("Dr. med. final", "Gutachter GEHEIM ist Dr. med." + R),
    ("Str. final", "Die Wohnung GEHEIM liegt an der Bahnhofstr." + R),
    ("Bahnhofstr. 5.", "Die Wohnung GEHEIM liegt an der Bahnhofstr. 5." + R),
    # domains / files
    ("domain final", "Mehr GEHEIM steht auf www.mieterverband.ch." + R),
    ("file name final", "Das GEHEIM steht in Mietvertrag_2023.pdf." + R),
    ("email final", "Kontakt GEHEIM: info@kanzlei.ch." + R),
    ("Art.5 no space final", "Massgebend GEHEIM ist Art.5." + R),
    ("Abs.2 no space final", "Massgebend GEHEIM ist Art. 5 Abs.2." + R),
    ("lit.a no space final", "Massgebend GEHEIM ist Art. 5 lit.a." + R),
    # quotes / parentheses
    ("quote close", "Er sagte GEHEIM: «Die Miete betraegt CHF 500.»" + R),
    ("quote close de", "Er sagte GEHEIM: „Die Miete betraegt CHF 500.“" + R),
    ("paren close", "(Die Miete GEHEIM betraegt CHF 500.)" + R),
    ("paren after stop abbrev", "Die Miete GEHEIM (inkl. MwSt.)" + R),
    ("bracket close", "Die Miete GEHEIM betraegt CHF 500.]" + R),
    # ? and !
    ("? glued", "Ist das GEHEIM zulaessig?Die Frist betraegt drei Monate [1]."),
    ("! glued", "Das GEHEIM ist wichtig!Die Frist betraegt drei Monate [1]."),
    ("?! ", "Ist das GEHEIM zulaessig?!" + R),
    ("? quote", "Ist das GEHEIM «zulaessig?»" + R),
    ("full-width ?", "Ist das GEHEIM zulaessig？" + R),
    ("full-width !", "Das ist GEHEIM wichtig！" + R),
    ("full-width .", "Das ist GEHEIM wichtig．" + R),
    ("ideographic stop", "Das ist GEHEIM wichtig。" + R),
    # ellipses
    ("ellipsis ...", "Die Miete GEHEIM betraegt viel..." + R),
    ("ellipsis char", "Die Vermieterin GEHEIM schwieg…" + R),
    ("ellipsis char glued", "Die Vermieterin GEHEIM schwieg…Die Frist betraegt drei Monate [1]."),
    # markdown
    ("bold sentence", "**Die Kuendigung GEHEIM ist nichtig.**" + R),
    ("italic sentence", "*Die Kuendigung GEHEIM ist nichtig.*" + R),
    ("underscore italic", "_Die Kuendigung GEHEIM ist nichtig._" + R),
    ("code span", "`Die Kuendigung GEHEIM ist nichtig.`" + R),
    ("bold label", "**Ergebnis:** Die Kuendigung GEHEIM ist nichtig." + R),
    ("heading inline", "## Ergebnis GEHEIM" + R),
    ("numbered list one line", "1. Die Miete GEHEIM betraegt CHF 500. 2. Die Frist betraegt drei Monate [1]."),
    ("numbered list lines", "1. Die Miete GEHEIM betraegt CHF 500\n2. Die Frist betraegt drei Monate [1]."),
    ("bullet one line", "- Die Miete GEHEIM betraegt CHF 500 - Die Frist betraegt drei Monate [1]."),
    ("bullet *", "* Die Miete GEHEIM betraegt CHF 500.\n* Die Frist betraegt drei Monate [1]."),
    ("table row", "| Miete | GEHEIM CHF 500 | Frist | drei Monate [1] |"),
    ("link", "Die Miete GEHEIM steht [hier](http://x.ch/a.pdf)." + R),
    # reference forms
    ("ref mid then stop", "Laut [1] betraegt die Frist drei Monate. Die Miete GEHEIM betraegt CHF 500."),
    ("ref mid sentence", "Die Frist [1] betraegt drei Monate. Die Miete GEHEIM betraegt CHF 500."),
    ("multi refs", "Die Frist betraegt drei Monate [1][2]. Die Miete GEHEIM betraegt CHF 500."),
    ("multi refs list", "Die Frist betraegt drei Monate [1, 2]. die Miete GEHEIM betraegt CHF 500."),
    ("ref 9", "Die Miete GEHEIM betraegt CHF 500 [9]. Die Frist betraegt drei Monate [1]."),
    ("ref then colon", "Die Frist betraegt drei Monate [1]: danach GEHEIM sechs Monate."),
    ("ref then dash", "Die Frist betraegt drei Monate [1] – danach GEHEIM sechs Monate."),
    ("ref then paren", "Die Frist betraegt drei Monate [1] (danach GEHEIM sechs Monate)."),
    ("ref after stop then lower", "Die Frist betraegt drei Monate. [1] danach GEHEIM sechs Monate."),
    ("ref then stop quote", "Die Frist betraegt drei Monate [1].» Danach GEHEIM sechs Monate."),
    ("ref stop glued lower", "Die Frist betraegt drei Monate [1].danach GEHEIM sechs Monate."),
    ("ref fullwidth", "Die Frist betraegt drei Monate 【1】. danach GEHEIM sechs Monate."),
    ("ref fullwidth digits", "Die Frist betraegt drei Monate [１]. danach GEHEIM sechs Monate."),
    ("ref superscript", "Die Miete GEHEIM betraegt CHF 500¹. Die Frist betraegt drei Monate [1]."),
    ("ref (1)", "Die Miete GEHEIM betraegt CHF 500 (1). Die Frist betraegt drei Monate [1]."),
    ("ref [1] inside quote", "Die Frist betraegt «drei Monate [1].» Die Miete GEHEIM betraegt CHF 500."),
    ("ref stop paren", "(Die Frist betraegt drei Monate [1].) Die Miete GEHEIM betraegt CHF 500."),
    ("ref then ! lower", "Die Frist betraegt drei Monate [1]! danach GEHEIM sechs Monate."),
    ("ref then ?", "Gilt die Frist [1]? Ja, GEHEIM sechs Monate."),
    # spaces
    ("nbsp after stop", "Die Miete GEHEIM betraegt CHF 500. Die Frist betraegt drei Monate [1]."),
    ("narrow nbsp", "Die Miete GEHEIM betraegt CHF 500. Die Frist betraegt drei Monate [1]."),
    ("ideographic space", "Die Miete GEHEIM betraegt CHF 500.　Die Frist betraegt drei Monate [1]."),
    ("zero-width space", "Die Miete GEHEIM betraegt CHF 500.​Die Frist betraegt drei Monate [1]."),
    ("zero-width space word", "Die Miete GEHEIM ist hoch.​Die Frist betraegt drei Monate [1]."),
    ("word joiner", "Die Miete GEHEIM ist hoch.⁠Die Frist betraegt drei Monate [1]."),
    ("tab", "Die Miete GEHEIM ist hoch.\tDie Frist betraegt drei Monate [1]."),
    # no space
    ("glued amount", "Die Miete GEHEIM betraegt CHF 500.Die Frist betraegt drei Monate [1]."),
    ("glued year", "Der Vertrag GEHEIM stammt aus 2023.Die Frist betraegt drei Monate [1]."),
    ("glued lower", "Die Miete GEHEIM ist hoch.die Frist betraegt drei Monate [1]."),
    ("glued abbrev", "Die Miete GEHEIM ist CHF 2 Mio.Die Frist betraegt drei Monate [1]."),
    ("glued single letter", "Es gilt GEHEIM Variante B.Die Frist betraegt drei Monate [1]."),
    ("glued umlaut", "Die Miete GEHEIM ist zu hoch.Überdies betraegt die Frist drei Monate [1]."),
    ("glued quote", "Die Miete GEHEIM ist hoch.«Die Frist betraegt drei Monate [1].»"),
    ("glued paren", "Die Miete GEHEIM ist hoch.(Die Frist betraegt drei Monate [1].)"),
    ("glued bold", "Die Miete GEHEIM ist hoch.**Die Frist betraegt drei Monate [1].**"),
    # line breaks
    ("CRLF", "Die Miete GEHEIM betraegt CHF 500\r\nDie Frist betraegt drei Monate [1]."),
    ("CR", "Die Miete GEHEIM betraegt CHF 500\rDie Frist betraegt drei Monate [1]."),
    ("LS", "Die Miete GEHEIM betraegt CHF 500 Die Frist betraegt drei Monate [1]."),
    ("<br>", "Die Miete GEHEIM betraegt CHF 500<br>Die Frist betraegt drei Monate [1]."),
    # semicolon / colon without a reference
    ("semicolon before ref", "Die Miete GEHEIM betraegt CHF 500; die Frist betraegt drei Monate [1]."),
    ("colon two statements", "Die Miete GEHEIM betraegt CHF 500: Die Frist betraegt drei Monate [1]."),
    ("dash two statements", "Die Miete GEHEIM betraegt CHF 500 – Die Frist betraegt drei Monate [1]."),
    # ordinals / leads
    ("der 3.", "Der Mieter ist GEHEIM der 3." + R),
    ("je 2.", "Es gibt GEHEIM je 2." + R),
    ("mit 18.", "Der Mieter wurde GEHEIM volljaehrig mit 18." + R),
    ("pro 10.", "Die Busse GEHEIM betraegt 5 pro 10." + R),
    ("zwischen 1 und 5.", "Die Frist liegt GEHEIM zwischen 1 und 5." + R),
    ("Monat lowercase after ordinal", "Die Frist GEHEIM endet am 5. mai" + R),
    ("2-digit month", "Die Zahlung GEHEIM erfolgt am 31. 2023 wurde verlaengert [1]."),
    ("ordinal then month abbrev", "Die Frist GEHEIM endet am 5. Jan." + R),
    ("Mai ordinal start", "Die Frist endet GEHEIM am 5. Mai beginnt die neue Frist [1]."),
    ("number month start", "Es sind GEHEIM 12. Mai 2024 gilt die neue Frist [1]."),
    ("seit 3.", "Die Regel GEHEIM gilt seit 3." + R),
    # semicolon after a stop-ish
    ("question then ref", "Gilt die GEHEIM Frist? Die Frist betraegt drei Monate [1]."),
    ("exclaim then ref", "Achtung GEHEIM! Die Frist betraegt drei Monate [1]."),
]


def main():
    joins, ok = [], 0
    for label, answer in CASES:
        reply = reply_for(answer)
        shown = [(s.text, s.refs) for s in reply.sentences]
        leaked = [t for t, _ in shown if "GEHEIM" in t]
        if leaked:
            joins.append((label, answer, shown, reply.removed, reply.status))
        else:
            ok += 1
    print(f"cases={len(CASES)} safe={ok} joins={len(joins)}")
    for label, answer, shown, removed, status in joins:
        print("JOIN", repr(label))
        print("   in :", repr(answer))
        print("   out:", repr(shown), "removed=", removed, "status=", status)


if __name__ == "__main__":
    main()
