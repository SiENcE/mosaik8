"""Unused prelude helpers leave a cc65 build (the Lynx and the PC Engine).

The cc65 prelude used to define its general helpers (`gbs_delay`,
`gbs_print_string`, `gbs_clear_area`, `gbs_hw_read`, ...) in every program,
called or not, and ld65 links a translation unit whole: the Lynx shooter
carried ~700 B of them in its 48 KB MAIN. A cc65 build is ONE C translation
unit plus the generated assembly units (`asm_units`), so whether a helper is
used is a fact about the finished text, and a pass over it answers it for
every helper at once, including the ones only another helper calls
(`gbs_print_number` -> `gbs_print_string`): it runs to a fixed point.

The rule is deliberately narrow:

  * only a FUNCTION DEFINITION whose name starts with `gbs_` is removed (the
    program's own functions have tree-shaking; data and prototypes stay);
  * a name counts as used when it appears anywhere else as an identifier, in
    a preprocessor line, inside a string literal (cc65 inline assembly names
    `_gbs_x` there) or in an assembly unit - everything but a comment and its
    own declarators (prototype or definition) and body;
  * a definition whose line holds anything before the return type is left
    alone.
"""

import re

_TOK = re.compile(r"""
    (?P<lc>//[^\n]*)
  | (?P<bc>/\*.*?\*/)
  | (?P<str>"(?:\\.|[^"\\\n])*")
  | (?P<chr>'(?:\\.|[^'\\\n])*')
  | (?P<pp>^[ \t]*\#(?:\\\n|[^\n])*)
  | (?P<id>[A-Za-z_]\w*)
  | (?P<num>\d\w*)
  | (?P<p>[{}()])
  | (?P<o>[^\s\w{}()"'/\#]+|/|\#)
""", re.S | re.M | re.X)

_HELPER = re.compile(r'gbs_\w+')
_PREFIX = re.compile(r'[A-Za-z_][\w \t\*]*')


def _definitions_and_uses(text):
    """(defs, uses): defs = [(name, start, end, body_start, body_end)],
    uses = {name: [positions]} of every non-declarator occurrence."""
    toks = [(m.lastgroup, m.group(), m.start(), m.end()) for m in _TOK.finditer(text)]
    defs, uses = [], {}
    depth = 0
    prev = None                         # the last significant token at any depth
    i = 0
    n = len(toks)
    while i < n:
        kind, val, s, e = toks[i]
        if kind in ('lc', 'bc'):
            i += 1
            continue
        if kind in ('str', 'pp'):
            for m in _HELPER.finditer(val):
                uses.setdefault(m.group(), []).append(s + m.start())
        elif kind == 'p':
            if val == '{':
                depth += 1
            elif val == '}':
                depth -= 1
        elif kind == 'id' and val.startswith('gbs_'):
            nxt = i + 1
            while nxt < n and toks[nxt][0] in ('lc', 'bc'):
                nxt += 1
            is_decl = (depth == 0 and nxt < n and toks[nxt][1] == '('
                       and prev is not None
                       and (prev[0] == 'id' or set(prev[1]) == {'*'}))
            if not is_decl:
                uses.setdefault(val, []).append(s)
            else:
                d = _definition_at(text, toks, i, nxt)
                if d is not None:
                    defs.append((val,) + d)
        prev = (kind, val)
        i += 1
    return defs, uses


def _definition_at(text, toks, i, lparen):
    """The definition whose declarator is token i, or None (a prototype, or
    a line this pass does not dare to cut)."""
    n = len(toks)
    k, par = lparen, 0
    while k < n:
        kind, val = toks[k][0], toks[k][1]
        if kind == 'p' and val == '(':
            par += 1
        elif kind == 'p' and val == ')':
            par -= 1
            if par == 0:
                break
        k += 1
    k += 1
    while k < n and toks[k][0] in ('lc', 'bc'):
        k += 1
    if k >= n or toks[k][1] != '{':
        return None
    body_start = toks[k][2]
    depth = 0
    while k < n:
        kind, val = toks[k][0], toks[k][1]
        if kind == 'p' and val == '{':
            depth += 1
        elif kind == 'p' and val == '}':
            depth -= 1
            if depth == 0:
                break
        k += 1
    if k >= n:
        return None
    body_end = toks[k][3]
    decl = toks[i][2]
    start = text.rfind('\n', 0, decl) + 1
    if not _PREFIX.fullmatch(text[start:decl]):
        return None
    end = body_end
    eol = text.find('\n', end)
    if eol == -1:
        eol = len(text)
    if text[end:eol].strip():
        return None                     # something else shares the closing line
    end = min(eol + 1, len(text))
    return start, end, body_start, body_end


def prune_unused_helpers(text, asm_texts=()):
    """`text` without the definitions of `gbs_` functions nothing uses, and
    the sorted names removed."""
    asm = "\n".join(asm_texts)
    asm_names = set(_HELPER.findall(asm))
    removed = []
    while True:
        defs, uses = _definitions_and_uses(text)
        dead = []
        for name, start, end, b0, b1 in defs:
            if name in asm_names:
                continue
            if any(not (b0 <= p < b1) for p in uses.get(name, ())):
                continue
            dead.append((name, start, end))
        if not dead:
            break
        # Two definitions of one name (alternatives under #if) go together;
        # cut from the end so earlier offsets stay valid.
        for name, start, end in sorted(dead, key=lambda d: d[1], reverse=True):
            text = text[:start] + text[end:]
            removed.append(name)
    return text, sorted(set(removed))
