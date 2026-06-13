import re


def strip_dot_leaders(text):
    """Remove dot leaders and collapse resulting whitespace.

    Two leader forms occur: runs of 2+ dots (the common case), and a
    single dot flanked by whitespace on both sides (a short leader the
    extract emits when only a couple of dots were scanned, e.g.
    ``Cantwells Bridge 1807,1810,1823,1846 . 150/75.00``). A space-
    isolated single dot is always a leader -- abbreviation periods attach
    to a letter/digit (``C.D.``, ``St.``, ``N.W.``), never sit space-
    isolated -- so collapsing it is safe and leaves real abbreviations
    untouched. Without this, the trailing leader residue survives value-
    stripping and blocks the manuscript date peel, gluing the dates into
    the post-office name."""
    t = re.sub(r'\.{2,}', ' ', str(text))
    t = re.sub(r'(?<=\s)\.(?=\s)', ' ', t)
    return re.sub(r'  +', ' ', t).strip()
