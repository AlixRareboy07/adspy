"""Devanagari -> Roman (Urdu/Hindi written in English letters, e.g. "abhi order karne ke liye").

Whisper writes spoken Urdu/Hindi in Devanagari; this turns it into the Roman style people type on
WhatsApp. No extra libraries.
"""
from __future__ import annotations

import re

V = {"\u0905": "a", "\u0906": "aa", "\u0907": "i", "\u0908": "ee", "\u0909": "u", "\u090a": "oo", "\u090b": "ri", "\u090f": "e", "\u0910": "ai",
     "\u0913": "o", "\u0914": "au", "\u0911": "o", "\u090d": "e"}
M = {"\u093e": "a", "\u093f": "i", "\u0940": "i", "\u0941": "u", "\u0942": "u", "\u0943": "ri", "\u0947": "e", "\u0948": "ai", "\u094b": "o",
     "\u094c": "au", "\u0949": "o", "\u0945": "e"}
C = {"\u0915": "k", "\u0916": "kh", "\u0917": "g", "\u0918": "gh", "\u0919": "n", "\u091a": "ch", "\u091b": "chh", "\u091c": "j", "\u091d": "jh",
     "\u091e": "n", "\u091f": "t", "\u0920": "th", "\u0921": "d", "\u0922": "dh", "\u0923": "n", "\u0924": "t", "\u0925": "th", "\u0926": "d",
     "\u0927": "dh", "\u0928": "n", "\u092a": "p", "\u092b": "f", "\u092c": "b", "\u092d": "bh", "\u092e": "m", "\u092f": "y", "\u0930": "r",
     "\u0932": "l", "\u0935": "w", "\u0936": "sh", "\u0937": "sh", "\u0938": "s", "\u0939": "h",
     "\u0915\u093c": "q", "\u0916\u093c": "kh", "\u0917\u093c": "gh", "\u091c\u093c": "z", "\u0921\u093c": "r", "\u0922\u093c": "rh", "\u092b\u093c": "f"}
NUKTA = "\u093c"
VIRAMA = "\u094d"
NASAL = {"\u0902": "n", "\u0901": "n", "\u0903": "h"}
DIGITS = {c: str(i) for i, c in enumerate("\u0966\u0967\u0968\u0969\u096a\u096b\u096c\u096d\u096e\u096f")}


def _word(w: str) -> str:
    # tokens: (roman, kind) kind: 'C' consonant+inherent a, 'V' vowel, 'X' other
    toks: list[list] = []
    i = 0
    while i < len(w):
        ch = w[i]
        if i + 1 < len(w) and w[i + 1] == NUKTA:
            ch = ch + NUKTA
            i += 1
        # precomposed nukta letters
        ch = {"\u0958": "\u0915\u093c", "\u0959": "\u0916\u093c", "\u095a": "\u0917\u093c", "\u095b": "\u091c\u093c", "\u095c": "\u0921\u093c",
              "\u095d": "\u0922\u093c", "\u095e": "\u092b\u093c"}.get(ch, ch)
        ch = ch.replace(NUKTA, "") if ch not in C else ch
        if ch in C:
            toks.append([C[ch], "C"])            # carries an inherent 'a' unless changed below
        elif ch in V:
            toks.append([V[ch], "V"])
        elif ch in M:
            if toks and toks[-1][1] == "C":
                toks[-1] = [toks[-1][0] + M[ch], "S"]   # consonant + vowel sign: no inherent a
            else:
                toks.append([M[ch], "V"])
        elif ch == VIRAMA:
            if toks and toks[-1][1] == "C":
                toks[-1] = [toks[-1][0], "S"]
        elif ch in NASAL:
            toks.append([NASAL[ch], "N"])
        elif ch in DIGITS:
            toks.append([DIGITS[ch], "X"])
        else:
            toks.append([ch, "X"])
        i += 1
    # schwa deletion (right to left): silent at word end, and between a vowel and a syllable that keeps its vowel
    n = len(toks)
    keep = [True] * n                      # does this token end with a vowel sound
    for k in range(n - 1, -1, -1):
        if toks[k][1] != "C":
            continue
        nxt = toks[k + 1] if k + 1 < n else None
        if nxt is None or nxt[1] == "X":
            keep[k] = False                # word-final: kar, ghar
        elif nxt[1] == "N":
            keep[k] = True                 # before anusvara keep the a
        elif k > 0 and toks[k - 1][1] in ("S", "V", "C") and keep[k - 1] and nxt[1] in ("S", "V") or \
                (k > 0 and toks[k - 1][1] in ("S", "V", "C") and nxt[1] == "C" and keep[k + 1]):
            keep[k] = False                # medial: karne, chalne, aapko
    out = []
    for k, (r, kind) in enumerate(toks):
        out.append(r + "a" if kind == "C" and keep[k] else r)
    return "".join(out)


def _vowelish(toks, k) -> bool:
    """previous syllable carries a vowel (so the schwa of this consonant is silent)."""
    p = toks[k - 1]
    if p[1] in ("S", "V"):
        return True
    if p[1] == "C":
        return True
    return False


_WORD = re.compile(r"[\u0900-\u097f]+")


COMMON = {"\u092e\u0947\u0902": "mein", "\u0939\u0948": "hai", "\u0939\u0948\u0902": "hain", "\u0928\u0939\u0940\u0902": "nahi", "\u092e\u0948\u0902": "main", "\u0914\u0930": "aur", "\u0915\u093e": "ka",
          "\u0915\u0940": "ki", "\u0915\u0947": "ke", "\u0915\u094b": "ko", "\u0938\u0947": "se", "\u092a\u0930": "par", "\u092d\u0940": "bhi", "\u092f\u0947": "yeh", "\u0935\u094b": "woh",
          "\u092f\u0939": "yeh", "\u0935\u0939": "woh", "\u0939\u0940": "hi", "\u0924\u094b": "to", "\u091c\u094b": "jo", "\u090f\u0915": "ek", "\u0906\u092a": "aap", "\u0939\u092e": "hum",
          "\u0915\u093f": "ke", "\u092f\u093e": "ya", "\u0905\u092c": "ab", "\u0905\u092d\u0940": "abhi", "\u0915\u0930": "kar", "\u0915\u0930\u0947\u0902": "karein", "\u0915\u0930\u094b": "karo",
          "\u0932\u093f\u090f": "liye", "\u0911\u0930\u094d\u0921\u0930": "order", "\u0906\u092a\u0915\u094b": "aapko", "\u0906\u092a\u0915\u0947": "aapke", "\u0906\u092a\u0915\u0940": "aapki", "\u0939\u094b": "ho",
          "\u0939\u094b\u0924\u093e": "hota", "\u0939\u094b\u0924\u0940": "hoti", "\u0939\u094b\u0924\u0947": "hote", "\u0930\u0939\u093e": "raha", "\u0930\u0939\u0940": "rahi", "\u0930\u0939\u0947": "rahe"}


def _conv(m: "re.Match") -> str:
    w = m.group(0)
    return COMMON.get(w) or _word(w)


def to_roman(text: str) -> str:
    out = _WORD.sub(_conv, text or "")
    out = out.replace("\u0964", ".").replace("\u0965", ".")
    return re.sub(r"\s+", " ", out).strip()


if __name__ == "__main__":
    import sys
    print(to_roman(sys.stdin.read()))
