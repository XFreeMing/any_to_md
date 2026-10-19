"""Conservative author-font cascade used before destructive code cleanup."""

from collections.abc import Callable, Iterable
import re

import cssselect2
import tinycss2
from bs4 import BeautifulSoup
from soupsieve.util import SelectorSyntaxError

FontEvidence = dict[int, bool | None]

_MONOSPACE_FAMILIES = {
    'monospace', 'ui-monospace', 'source code pro', 'courier', 'courier new',
    'consolas', 'menlo', 'monaco', 'dejavu sans mono', 'liberation mono',
    'fira code', 'fira mono', 'cascadia code', 'cascadia mono', 'noto sans mono',
}
_PROPORTIONAL_FAMILIES = {
    'serif', 'sans-serif', 'cursive', 'fantasy', 'system-ui', 'ui-serif',
    'ui-sans-serif', 'emoji', 'math', 'fangsong', 'arial', 'helvetica',
    'times', 'times new roman', 'dejavu sans', 'pingfang sc', 'pingfang tc',
    'monotype corsiva',
}


def _family(tokens) -> bool | str | None:
    tokens = [token for token in tokens if token.type != 'comment']
    value = tinycss2.serialize(tokens).strip().lower()
    if value in {'inherit', 'unset'}:
        return 'inherit'
    if value == 'initial':
        return False
    if value in {'revert', 'revert-layer'} or 'var(' in value:
        return None
    if not value or any(token.type not in {'ident', 'string', 'whitespace'} and not (token.type == 'literal' and token.value == ',') for token in tokens):
        return None
    # Only the primary family supplies positive evidence. A serif primary font
    # followed by a monospace fallback does not make the source code-styled.
    primary = []
    for token in tokens:
        if token.type == 'literal' and token.value == ',':
            break
        primary.append(token)
    primary = [token for token in primary if token.type != 'whitespace']
    if len(primary) == 1 and primary[0].type == 'string':
        name = primary[0].value
    elif primary and all(token.type == 'ident' for token in primary):
        name = ' '.join(token.value for token in primary)
    else:
        return None
    name = ' '.join(name.casefold().split())
    if name in _MONOSPACE_FAMILIES:
        return True
    if name in _PROPORTIONAL_FAMILIES:
        return False
    return None


def author_font_evidence(soup: BeautifulSoup, styles: Iterable[tuple[str, bool]], report: Callable[[str], None]) -> FontEvidence:
    """Resolve importance, specificity, source order, inline style and inheritance.

    Conditional/layered rules and unsupported selectors never authorize deleting
    text. They make affected elements uncertain instead. An unresolved import
    makes the whole document uncertain because it could override any selector.
    """
    specified = {}
    uncertain = set()
    order = 0
    unresolved_import = False

    def declarations(tokens):
        return [item for item in tinycss2.parse_declaration_list(tokens, skip_comments=True, skip_whitespace=True) if item.type == 'declaration' and item.lower_name in {'font-family', 'font', 'all'}]

    def apply(elements, values, specificity, inline=False, conditional=False):
        nonlocal order
        for declaration in values:
            order += 1
            value = _family(declaration.value) if declaration.lower_name == 'font-family' else None
            for element in elements:
                identity = id(element)
                if conditional:
                    uncertain.add(identity)
                    continue
                priority = (declaration.important, inline, specificity, order)
                if identity not in specified or priority >= specified[identity][0]:
                    specified[identity] = (priority, value)

    def visit(rules, conditional=False):
        nonlocal unresolved_import
        for rule in rules:
            if rule.type == 'at-rule':
                if rule.lower_at_keyword == 'import':
                    unresolved_import = True
                elif rule.content is not None and rule.lower_at_keyword not in {'font-face', 'page', 'keyframes', '-webkit-keyframes'}:
                    visit(tinycss2.parse_rule_list(rule.content, skip_comments=True, skip_whitespace=True), conditional=True)
            elif rule.type == 'qualified-rule':
                if any(token.type == '{} block' and re.search(r'\b(?:font|font-family|all)\s*:', tinycss2.serialize(token.content)) for token in rule.content):
                    # Nested selectors can change descendants even when the
                    # outer declaration parser only sees the parent's font.
                    report('嵌套字体规则无法唯一判定，保留源编号。')
                    uncertain.update(id(element) for element in soup.find_all(True))
                values = declarations(rule.content)
                if not values:
                    continue
                # Top-level comma tokens split selector lists, while commas in
                # :is()/:not() remain inside their function token.
                selectors, part = [], []
                for token in rule.prelude:
                    if token.type == 'literal' and token.value == ',':
                        selectors.append(part)
                        part = []
                    else:
                        part.append(token)
                selectors.append(part)
                for tokens in selectors:
                    try:
                        compiled = cssselect2.compile_selector_list(tokens)
                        if any(selector.pseudo_element is not None for selector in compiled):
                            continue
                        elements = soup.select(tinycss2.serialize(tokens))
                        apply(elements, values, max(selector.specificity for selector in compiled), conditional=conditional)
                    except (cssselect2.SelectorError, SelectorSyntaxError, NotImplementedError, ValueError):
                        report('无法确定字体选择器的层叠，保留源编号。')
                        uncertain.update(id(element) for element in soup.find_all(True))

    for css, conditional in styles:
        visit(tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True), conditional)
    for element in soup.find_all(style=True):
        apply([element], declarations(element['style']), (0, 0, 0), inline=True)
    if unresolved_import:
        report('字体判定存在未展开的样式导入，保留源编号。')
        return {id(element): None for element in soup.find_all(True)}
    if uncertain:
        report('条件或分层字体规则无法唯一判定，相关正文保留源编号。')
    effective = {}
    for element in soup.find_all(True):
        inherited = effective.get(id(element.parent), False)
        value = specified[id(element)][1] if id(element) in specified else 'inherit'
        effective[id(element)] = None if id(element) in uncertain else inherited if value == 'inherit' else value
    if any(value is None for value in effective.values()):
        report('字体或层叠无法唯一判定，相关正文保留源编号。')
    return effective
