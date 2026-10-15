#!/usr/bin/env python3
"""Проверка ссылок справочника.

Запуск из любого места:  python3 tools/check-refs.py [--fix]

Проверяет:
  * ссылки на файлы и якоря (`[текст](файл.md#sN.N)`) — файл и якорь существуют;
  * текст ссылки «§N.N» совпадает с якорем `#sN.N`;
  * перед каждым нумерованным заголовком стоит якорь `<a id="sN.N"></a>`;
  * нумерация разделов внутри главы непрерывна;
  * нет «голых» ссылок §N.N — без метки источника и без якоря.

С ключом --fix «голые» ссылки превращаются в кликабельные, недостающие якоря
добавляются. Код возврата 1, если остались ошибки.
"""
import os
import re
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY = '21-реестр-расхождений.md'  # §21.5 живёт здесь, в главе 21 — только отсылка
HEAD = re.compile(r'^(#{1,6}) (\d+(?:\.\d+)*)\. ')
ANCHOR = re.compile(r'^<a id="(s[\d.]+)"></a>$')
LINK = re.compile(r'\[([^\]\[]*)\]\(([^)\s]*?)(?:#([^)\s]+))?\)')
# «§ N» с пробелом — ссылка на раздел источника, её не трогаем
BARE = re.compile(r'§(\d+(?:\.\d+)*)(\s*[–-]\s*(\d+(?:\.\d+)*))?')


def md_files():
    out = []
    for d, _, names in os.walk(ROOT):
        rel = os.path.relpath(d, ROOT)
        # служебные каталоги и архив прежних редакций (_архив) не проверяются
        if rel != '.' and (rel.split(os.sep)[0].startswith(('.', '_')) or rel.split(os.sep)[0] == 'tools'):
            continue
        out += [os.path.relpath(os.path.join(d, n), ROOT) for n in names if n.endswith('.md')]
    return sorted(out)


def lines_outside_code(text):
    """(номер строки, строка, внутри ли блока кода)."""
    inc = False
    for i, l in enumerate(text.split('\n')):
        if l.startswith('```'):
            yield i, l, True
            inc = not inc
            continue
        yield i, l, inc


def collect(files, texts):
    home, heads = {}, []
    for f in files:
        for i, l, code in lines_outside_code(texts[f]):
            m = None if code else HEAD.match(l)
            if not m:
                continue
            num = m.group(2)
            heads.append((f, i, num))
            if num not in home or f == REGISTRY:
                home[num] = f
    return home, heads


def href(num, src, home):
    tgt = home[num]
    if tgt == src:
        return '#s' + num
    return os.path.relpath(tgt, os.path.dirname(src) or '.') + '#s' + num


def unclosed_bracket(pre):
    return pre.rfind('[') > pre.rfind(']')


def fix_bare(line, src, home):
    out, pos = [], 0
    for m in BARE.finditer(line):
        pre = line[:m.start()]
        if unclosed_bracket(pre) or pre.count('`') % 2 or m.group(1) not in home:
            continue
        rep = '[§%s](%s)' % (m.group(1), href(m.group(1), src, home))
        if m.group(2):
            b = m.group(3)
            sep = m.group(2)[:len(m.group(2)) - len(b)]
            rep += sep + ('[%s](%s)' % (b, href(b, src, home)) if b in home else b)
        out.append(line[pos:m.start()] + rep)
        pos = m.end()
    out.append(line[pos:])
    return ''.join(out)


def main():
    fix = '--fix' in sys.argv
    os.chdir(ROOT)
    files = md_files()
    texts = {f: open(f, encoding='utf-8').read() for f in files}
    home, heads = collect(files, texts)
    errors = []

    if fix:
        for f in files:
            res = []
            for i, l, code in lines_outside_code(texts[f]):
                m = None if code else HEAD.match(l)
                if m and home.get(m.group(2)) == f:
                    if not (res and res[-1] == '' and len(res) > 1 and ANCHOR.match(res[-2])):
                        while len(res) > 1 and res[-1] == '' and res[-2] == '':
                            res.pop()
                        if res and res[-1] != '':
                            res.append('')
                        res += ['<a id="s%s"></a>' % m.group(2), '']
                elif not code and not m:
                    l = fix_bare(l, f, home)
                res.append(l)
            new = '\n'.join(res)
            if new != texts[f]:
                open(f, 'w', encoding='utf-8').write(new)
                texts[f] = new
                print('исправлен', f)

        home, heads = collect(files, texts)  # номера строк сдвинулись

    anchors = {f: set(re.findall(r'<a id="([^"]+)"></a>', texts[f])) for f in files}

    # якорь перед каждым заголовком
    for f, i, num in heads:
        if home.get(num) != f:
            continue
        L = texts[f].split('\n')
        j = i - 1
        while j >= 0 and L[j] == '':
            j -= 1
        if j < 0 or L[j] != '<a id="s%s"></a>' % num:
            errors.append('%s:%d: у заголовка %s нет якоря' % (f, i + 1, num))

    # непрерывность нумерации
    kids = defaultdict(set)
    for _, _, num in heads:
        if '.' in num:
            p, k = num.rsplit('.', 1)
            kids[p].add(int(k))
    for p, ks in sorted(kids.items()):
        if sorted(ks) != list(range(1, max(ks) + 1)):
            errors.append('нумерация разделов %s.x прерывается: %s' % (p, sorted(ks)))

    # ссылки и «голые» §
    for f in files:
        for i, l, code in lines_outside_code(texts[f]):
            if code:
                continue
            for m in LINK.finditer(l):
                text, path, frag = m.groups()
                tgt = os.path.normpath(os.path.join(os.path.dirname(f), path)) if path else f
                if path.startswith(('http:', 'https:', 'mailto:')):
                    continue
                if path and not os.path.exists(tgt):
                    errors.append('%s:%d: нет файла %s' % (f, i + 1, path))
                elif frag and frag not in anchors.get(tgt, ()):
                    errors.append('%s:%d: в %s нет якоря #%s' % (f, i + 1, path or f, frag))
                t = text.lstrip('§')
                if frag and frag.startswith('s') and re.fullmatch(r'[\d.]+', t) and frag != 's' + t:
                    errors.append('%s:%d: текст «%s» ведёт на #%s' % (f, i + 1, text, frag))
            if HEAD.match(l):
                continue
            for m in BARE.finditer(l):
                pre = l[:m.start()]
                if pre.endswith('[') or unclosed_bracket(pre) or pre.count('`') % 2:
                    continue
                errors.append('%s:%d: ссылка §%s без якоря%s' % (
                    f, i + 1, m.group(1), '' if m.group(1) in home else ' и такого раздела нет'))

    for e in errors:
        print(e)
    print('файлов: %d, разделов: %d, ошибок: %d' % (len(files), len(home), len(errors)))
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
