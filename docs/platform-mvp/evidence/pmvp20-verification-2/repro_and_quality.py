"""Throwaway: natural reproductions for the report, quality losses, and item 4."""
import sys

sys.path.insert(0, sys.argv[1])  # P/src

from assistant.service import NO_ANSWER_TEXT, SYSTEM_INSTRUCTION, AssistantService  # noqa: E402

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


def show(title, cases):
    print("=" * 10, title)
    for answer in cases:
        r = reply_for(answer)
        print(repr(answer))
        print("  ->", r.status, [(s.text, s.refs) for s in r.sentences], "removed=", r.removed)


show("JOINS (natural)", [
    "Der Streitwert beträgt CHF 2 Mio. Die Frist beträgt drei Monate [1].",
    "Die Miete beträgt 1'500 Fr. Die Kündigungsfrist beträgt drei Monate [1].",
    "Die Miete beträgt CHF 1'500 inkl. MwSt. Die Kündigungsfrist beträgt drei Monate [1].",
    "**Die Kündigung ist nichtig.** Die Frist beträgt drei Monate [1].",
    "Die Kündigung ist anfechtbar nach Art. 271a Abs. 1 lit. a. Die Frist beträgt 30 Tage [1].",
    "Massgebend sind Art. 271 f. OR. Die Frist beträgt 30 Tage [1].",
    "Massgebend sind Art. 271 f. Die Frist beträgt 30 Tage [1].",
    "Die Details stehen in Mietvertrag_2023.pdf. Die Frist beträgt drei Monate [1].",
    "Dies gilt für Miet- und Pachtverträge u.a. Die Frist beträgt drei Monate [1].",
    "Vertragspartei ist die Müller & Co. Die Frist beträgt drei Monate [1].",
    "Die Miete beträgt CHF 500.Die Frist beträgt drei Monate [1].",
    "Die Zahl der Mieter stieg auf 12. Die Frist beträgt drei Monate [1].",
    "Die Miete ist fällig am 5. Die Frist beträgt drei Monate [1].",
    "Ist die Kündigung gültig?Die Frist beträgt drei Monate [1].",
    "Die Vermieterin schwieg… Die Frist beträgt drei Monate [1].",
    "Vermieterin ist B. 2023 kündigte sie den Vertrag [1].",
    "Die Miete beträgt CHF 500; die Frist beträgt drei Monate [1].",
])

show("ALREADY SAFE (spot checks)", [
    "Die Miete beträgt CHF 500. Die Frist beträgt drei Monate [1].",
    "Massgebend ist Art. 5 Abs. 2. Die Frist beträgt drei Monate [1].",
    "Massgebend ist Kapitel III. Die Frist beträgt drei Monate [1].",
    "Dies betrifft Mietverträge usw. Die Frist beträgt drei Monate [1].",
    "Massgebend sind Art. 271 ff. Die Frist beträgt 30 Tage [1].",
    "Die Frist beträgt drei Monate [1]. 2023 wurde sie verlängert.",
    "Die Frist beträgt drei Monate [1][2]. die Miete beträgt CHF 500.",
    "Die Frist beträgt drei Monate [9]. Die Miete beträgt CHF 500 [1].",
    "Die Miete beträgt CHF 500\r\nDie Frist beträgt drei Monate [1].",
    "Die Miete beträgt CHF 500. Die Frist beträgt drei Monate [1].",
    "Er sagte: «Die Miete beträgt CHF 500.» Die Frist beträgt drei Monate [1].",
    "(Die Miete beträgt CHF 500.) Die Frist beträgt drei Monate [1].",
    "Die Miete beträgt viel... Die Frist beträgt drei Monate [1].",
    "1. Die Miete beträgt CHF 500. 2. Die Frist beträgt drei Monate [1].",
    "## Ergebnis\n- Die Miete beträgt CHF 500\n- Die Frist beträgt drei Monate [1]",
])

show("QUALITY (referenced text cut)", [
    "Nach Art. 266a ff. OR gilt eine Frist von drei Monaten [1].",
    "Vgl. hierzu Art. 5 ff. ZGB und die Lehre [1].",
    "Hans M. Müller hat den Vertrag unterzeichnet [1].",
    "Die Miete ist jeweils am 1. und 15. jedes Monats fällig [1].",
    "Das Urteil vom 12. 3. 2021 bestätigt dies [1].",
    "Gemäss Ziff. 5. des Vertrags gilt eine Frist von drei Monaten [1].",
    "Gemäss Ziffer II. 3 des Vertrags gilt dies [1].",
    "Gemäss Urteil 4A_12/2020 E. 3.2. gilt dies [1].",
    "Die Parteien A. und B. haben den Vertrag geschlossen [1].",
    "Der Mieter, Hr. Dr. iur. M. Meier, hat gekündigt [1].",
    "Die Firma Müller & Co. AG hat gekündigt [1].",
    "Die Frist beginnt am 1. des Folgemonats [1].",
    "Die Miete beträgt CHF 1'500.- pro Monat [1].",
    "Gemäss BGE 140 III 115 E. 2.1 gilt dies [1].",
    "Das Urteil datiert vom 1. Okt. 2020 [1].",
    "Gemäss § 573 Abs. 2 Nr. 1 BGB gilt dies [1].",
    "Gemäss Art. 335c Abs. 1 OR beträgt die Frist im 1. Dienstjahr einen Monat [1].",
    "Die Frist beträgt 3 Mte. ab Zugang [1].",
    "Laut Vertrag (S. 4) gilt dies [1].",
    "Der Vertrag mit der XY GmbH i.L. ist gültig [1].",
    "Gemäss Art. 5 Abs. 2 Satz 2 Halbsatz 1 OR gilt dies [1].",
    "Die Gemeinde (Gde.) Muri b. Bern ist zuständig [1].",
    "Die Frist endet am 2. Jan. 2025 [1].",
    "Das gilt auch für Untermieter bzw. Mitbewohner [1].",
    "Die Prüfung erfolgt durch die Fa. Meier [1].",
    "Gemäss Art. 5 lit. a OR gilt dies [1].",
    "Die Miete beträgt Fr. 500.- [1].",
    "Er zahlte am 3. Mai, 4. Juni und 5. Juli [1].",
    "Nach Ablauf der 2. Frist ist die Kündigung wirksam [1].",
    "Gemäss Art. 2 Ziff. 3 Bst. b des Vertrags gilt dies [1].",
    "Gemäss Rz. 12 S. 3 gilt dies [1].",
    "Die Frist beträgt 30 Tage, vgl. Art. 273 Abs. 1 OR [1].",
    "Laut Prof. Dr. Müller ist das so [1].",
])

show("ITEM 4 (no-answer)", [
    NO_ANSWER_TEXT + " [1].",
    NO_ANSWER_TEXT + ".",
    "**" + NO_ANSWER_TEXT + " [1].**",
    "«" + NO_ANSWER_TEXT + "» [1].",
    "Antwort: " + NO_ANSWER_TEXT + " [1].",
    "Leider: " + NO_ANSWER_TEXT + " [1].",
    "- " + NO_ANSWER_TEXT + " [1]",
    NO_ANSWER_TEXT.upper() + " [1].",
    "Dazu finde ich leider nichts in Ihren Quellen [1].",
    NO_ANSWER_TEXT + ", aber die Frist beträgt drei Monate [1].",
    "Die Frist beträgt drei Monate [1]. " + NO_ANSWER_TEXT + " [2].",
    "Die Frist beträgt drei Monate [1].\n" + NO_ANSWER_TEXT,
])
print("=" * 10, "SYSTEM_INSTRUCTION")
print(SYSTEM_INSTRUCTION)
