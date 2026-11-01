#!/usr/bin/env python3
"""N1-08. CSS contract linter for NextLife templates.

Fails if:
- Class from template is absent in app.css / base.html inline <style>
- A Tailwind-like utility name is used while Tailwind is not in the project
- Template has style= attribute (violates U-4)
- Alpine.js directive used but Alpine not loaded in base.html
- htmx directive used but htmx not loaded in base.html

Exit code 0 = PASS, 1 = FAIL.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / "templates" / "web"
APP_CSS = ROOT / "src" / "nextlife" / "api" / "static" / "app.css"

TAILWIND_PATTERNS = re.compile(
    r'^('
    r'grid-cols-\w+|space-[xy]-\w+|flex-\w+|'
    r'text-(xs|sm|base|lg|xl|2xl|3xl|white|black|'
    r'slate-\d{2,3}|gray-\d{2,3}|red-\d{2,3}|blue-\d{2,3}|green-\d{2,3}|'
    r'amber-\d{2,3}|emerald-\d{2,3}|yellow-\d{2,3}|purple-\d{2,3}|'
    r'indigo-\d{2,3}|pink-\d{2,3}|rose-\d{2,3}|teal-\d{2,3}|cyan-\d{2,3}|'
    r'orange-\d{2,3}|violet-\d{2,3}|fuchsia-\d{2,3}|\[\w+\])|'
    r'bg-(white|black|transparent|current|'
    r'slate-\d{2,3}|gray-\d{2,3}|red-\d{2,3}|blue-\d{2,3}|green-\d{2,3}|'
    r'amber-\d{2,3}|emerald-\d{2,3}|yellow-\d{2,3}|purple-\d{2,3}|'
    r'indigo-\d{2,3}|orange-\d{2,3}|teal-\d{2,3}|cyan-\d{2,3}|'
    r'black/\d+|slate-\d+/\d+)|'
    r'border-(t|b|l|r|x|y|slate-\d{2,3}|blue-\d{2,3}|gray-\d{2,3}|'
    r'amber-\d{2,3}|red-\d{2,3}|green-\d{2,3}|dashed|dotted|solid)|'
    r'(p|px|py|pl|pr|pt|pb|m|mx|my|ml|mr|mt|mb)-(\d+(\.\d+)?)|'
    r'(w|h|min-w|min-h|max-w|max-h)-(\d+|full|screen|auto|\[\w+\])|'
    r'(rounded|shadow|font|leading|tracking|overflow|object|aspect|cursor|'
    r'select|pointer|opacity|transition|transform|scale|rotate|translate|'
    r'skew|origin|duration|ease|delay|animate|from|via|to|'
    r'col|row|order|float|clear|list|appearance|outline|ring|divide|'
    r'place|content|self|items|justify|gap)-\w+|'
    r'hover:\S+|focus:\S+|active:\S+|disabled:\S+|'
    r'block|inline(-block|-flex)?|uppercase|lowercase|capitalize|'
    r'underline|overline|line-through|no-underline|'
    r'truncate|text-ellipsis|whitespace-\w+|break-\w+|'
    r'sr-only|not-sr-only|visible|invisible|'
    r'absolute|relative|sticky|static|'
    r'(top|right|bottom|left|inset)-\d+|'
    r'(z|order)-\d+|'
    r'font-(thin|extralight|light|normal|medium|semibold|bold|extrabold|black|mono|sans|serif)|'
    r'mx-auto|'
    r'\w+-\[[\w#%./,]+\]'
    r')$'
)

ALPINE_DIRECTIVE = re.compile(
    r'(?<![/\w])(?:x-(?:data|model|for|if|show|text|bind|ref|on|init|effect|'
    r'teleport|ignore|cloak|transition|intersect|spread|html)|'
    r'@[a-z][a-z0-9.:]+(?==)|'
    r':[a-z][a-z0-9\-:.]+(?==))'
)
HTMX_DIRECTIVE = re.compile(r'\shx-[a-z]')


def collect_declared_classes():
    declared = set()
    if APP_CSS.exists():
        for m in re.finditer(r'\.([a-zA-Z][a-zA-Z0-9_-]*)', APP_CSS.read_text(encoding="utf-8")):
            declared.add(m.group(1))
    base = TEMPLATES_DIR / "base.html"
    if base.exists():
        base_text = base.read_text(encoding="utf-8")
        for style_block in re.finditer(r'<style[^>]*>(.*?)</style>', base_text, re.DOTALL):
            for m in re.finditer(r'\.([a-zA-Z][a-zA-Z0-9_-]*)', style_block.group(1)):
                declared.add(m.group(1))
    return declared


def check_base_html_scripts():
    """Возвращает (alpine_loaded, htmx_loaded, external_sources).

    external_sources — любые http(s) источники в <script src>/<link href>
    (нарушение N3-01: CDN запрещён, библиотеки — только static/vendor/).
    """
    base = TEMPLATES_DIR / "base.html"
    if not base.exists():
        return False, False, []
    text = base.read_text(encoding="utf-8")
    scripts = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', text)
    links = re.findall(r'<link[^>]+href=["\']([^"\']+)["\']', text)
    alpine = any('alpine' in s.lower() for s in scripts)
    htmx = any('htmx' in s.lower() for s in scripts)
    external = [s for s in scripts + links if s.startswith('http://') or s.startswith('https://')]
    return alpine, htmx, external


def collect_documented_classes():
    """U-5: множество классов, описанных в docs/ui-components.md.

    Класс считается задокументированным, только если в документации
    встречается его точное имя в обратных кавычках с точкой: `` `.имя` ``.
    Это исключает ложные срабатывания по абзацам («e.g.», «т.д.»).
    """
    doc = ROOT / "docs" / "ui-components.md"
    if not doc.exists():
        return set()
    text = doc.read_text(encoding="utf-8")
    documented = set()
    for m in re.finditer(r'`\.([a-zA-Z][a-zA-Z0-9_-]*)`', text):
        documented.add(m.group(1))
    return documented


def lint_template(path, declared, documented, alpine_loaded, htmx_loaded):
    errors = []
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    for lineno, line in enumerate(lines, 1):
        # U-4: No inline style= attributes
        if re.search(r'<[a-zA-Z][^>]+style=["\'][^"\']*["\']', line):
            errors.append(f"  [{path.name}:{lineno}] U-4: inline style= in template element")

        # U-1: Classes not declared in CSS.
        # Jinja-блоки заменяем ПРОБЕЛОМ на уровне СТРОКИ, а не удаляем:
        # каждая ветвь {% if %} — отдельный класс (удаление склеивает их),
        # а кавычки внутри {{ ... }} ломают разбор атрибута class="...".
        cleaned_line = re.sub(r'\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}', ' ', line)
        for m in re.finditer(r'class=["\']([^"\']+)["\']', cleaned_line):
            for cls in m.group(1).split():
                cls = cls.strip("'\"")
                if not cls:
                    continue
                # TAILWIND проверяется ПЕРВЫМ: имя в стиле утилит Tailwind —
                # нарушение независимо от того, объявлен ли класс в CSS
                # (иначе класс можно «отмыть», объявив его в app.css).
                if TAILWIND_PATTERNS.match(cls):
                    errors.append(
                        f"  [{path.name}:{lineno}] TAILWIND: '{cls}' is Tailwind-like "
                        f"(Tailwind not in project)"
                    )
                elif cls not in declared:
                    errors.append(f"  [{path.name}:{lineno}] U-1: class '{cls}' not declared in CSS")
                elif documented is not None and cls not in documented:
                    errors.append(f"  [{path.name}:{lineno}] U-5: class '{cls}' not documented in docs/ui-components.md")

        # U-2: Alpine directives without Alpine loaded
        if not alpine_loaded and ALPINE_DIRECTIVE.search(line):
            errors.append(f"  [{path.name}:{lineno}] U-2: Alpine directive without Alpine loaded")

        # U-2: htmx directives without htmx loaded
        if not htmx_loaded and HTMX_DIRECTIVE.search(line):
            errors.append(f"  [{path.name}:{lineno}] U-2: htmx directive without htmx loaded")

    return errors


def main():
    declared = collect_declared_classes()
    documented = collect_documented_classes()
    alpine_loaded, htmx_loaded, external = check_base_html_scripts()

    print("CSS contract linter — NextLife")
    print(f"CSS classes declared: {len(declared)} | documented: {len(documented)}")
    print(f"Alpine.js loaded: {alpine_loaded} | htmx loaded: {htmx_loaded} | external sources: {len(external)}")
    print()

    all_errors = []

    # N3-01: CDN запрещён — только static/vendor/
    for src in external:
        all_errors.append(f"  [base.html] N3-01: external (CDN) source '{src}' — vendor into static/vendor/")

    for tpl in sorted(TEMPLATES_DIR.glob("*.html")):
        errs = lint_template(tpl, declared, documented, alpine_loaded, htmx_loaded)
        all_errors.extend(errs)

    if all_errors:
        groups = {
            'TAILWIND': [e for e in all_errors if 'TAILWIND' in e],
            'U-1': [e for e in all_errors if 'U-1:' in e and 'TAILWIND' not in e],
            'U-2': [e for e in all_errors if 'U-2:' in e],
            'U-4': [e for e in all_errors if 'U-4' in e],
            'U-5': [e for e in all_errors if 'U-5' in e],
            'N3-01': [e for e in all_errors if 'N3-01' in e],
        }
        print(f"FAIL — {len(all_errors)} violation(s):")
        for label in ('TAILWIND', 'U-1', 'U-2', 'U-4', 'U-5', 'N3-01'):
            items = groups[label]
            if not items:
                continue
            print(f"\n[{label}] {len(items)} violation(s):")
            for e in items[:15]:
                print(e)
            if len(items) > 15:
                print(f"  ... and {len(items) - 15} more")
        return 1
    else:
        print("PASS — no CSS contract violations found.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
