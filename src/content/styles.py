"""Scope author styles to their source document and rewrite resource URLs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import tinycss2

MAX_STYLE_BYTES = 16 * 1024 * 1024
MAX_STYLE_RULES = 100000
MAX_STYLE_DEPTH = 64


class StyleLimit(ValueError):
    pass


@dataclass
class StyleBudget:
    bytes: int = 0
    rules: int = 0

    def reserve(self, *, size: int = 0, rules: int = 0) -> None:
        if self.bytes+size > MAX_STYLE_BYTES or self.rules+rules > MAX_STYLE_RULES:
            raise StyleLimit('样式展开超过修订累计大小或规则工作量限制。')
        self.bytes += size
        self.rules += rules


def _urls(tokens, resolver: Callable[[str], str]) -> None:
    for token in tokens:
        if token.type == "url":
            value = resolver(token.value)
            token.value = value
            token.representation = 'url("' + value.replace('"', '%22') + '")'
        elif token.type == "function" and token.lower_name == "url":
            value = tinycss2.serialize(token.arguments).strip().strip("'\"")
            resolved = resolver(value).replace('"', '%22')
            token.arguments = tinycss2.parse_component_value_list('"' + resolved + '"')
        elif token.type == "function":
            _urls(token.arguments, resolver)
        elif getattr(token, "content", None) is not None:
            _urls(token.content, resolver)


def rewrite_declarations(css: str, resolver: Callable[[str], str]) -> str:
    tokens = tinycss2.parse_component_value_list(css)
    _urls(tokens, resolver)
    return tinycss2.serialize(tokens)


def style_references(css: str) -> list[str]:
    """Find raw CSS dependencies when freezing an input, before normalization."""
    result = []
    def collect(value):
        result.append(value)
        return value
    _urls(tinycss2.parse_component_value_list(css), collect)
    for rule in tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True):
        if rule.type == 'at-rule' and rule.lower_at_keyword == 'import':
            try:
                result.append(_import_parts(rule.prelude)[0])
            except ValueError:
                pass  # The normalizer reports invalid author CSS explicitly.
    return list(dict.fromkeys(result))


def rewrite_stylesheet_resources(css: str, resolver: Callable[[str], str]) -> str:
    """Rewrite stylesheet URLs, including the string form of @import."""
    rules = tinycss2.parse_stylesheet(css)
    for rule in rules:
        if rule.type == 'at-rule' and rule.lower_at_keyword == 'import':
            for index, token in enumerate(rule.prelude):
                if token.type in {'whitespace', 'comment'}:
                    continue
                if token.type == 'string':
                    value = resolver(token.value).replace('"', '%22')
                    rule.prelude[index:index+1] = tinycss2.parse_component_value_list('"'+value+'"')
                break
        if getattr(rule, 'prelude', None) is not None:
            _urls(rule.prelude, resolver)
        if getattr(rule, 'content', None) is not None:
            _urls(rule.content, resolver)
    return tinycss2.serialize(rules)


def _import_parts(prelude) -> tuple[str, str | None, str | None, str]:
    """Read the import URL and its layer/supports/media conditions as CSS tokens."""
    tokens = [token for token in prelude if token.type not in {"whitespace", "comment"}]
    if not tokens:
        raise ValueError("@import 缺少样式地址")
    first, *conditions = tokens
    if first.type in {"url", "string"}:
        target = first.value
    elif first.type == "function" and first.lower_name == "url":
        arguments = [token for token in first.arguments if token.type not in {"whitespace", "comment"}]
        if len(arguments) != 1 or arguments[0].type != "string":
            raise ValueError("@import 含无效的 url()")
        target = arguments[0].value
    else:
        raise ValueError("@import 含无效的样式地址")
    layer = None
    supports = None
    if conditions and conditions[0].type == "ident" and conditions[0].lower_value == "layer":
        layer = ""
        conditions.pop(0)
    elif conditions and conditions[0].type == "function" and conditions[0].lower_name == "layer":
        layer = tinycss2.serialize(conditions.pop(0).arguments).strip()
    if conditions and conditions[0].type == "function" and conditions[0].lower_name == "supports":
        arguments = conditions.pop(0).arguments
        supports = tinycss2.serialize(arguments).strip()
        if any(token.type == "literal" and token.value == ":" for token in arguments):
            supports = f"({supports})"
    # Token serialization needs the original whitespace between media keywords.
    media = " ".join(tinycss2.serialize([token]) for token in conditions)
    return target, layer, supports, media


def scope_styles(
    css: str,
    scope: str,
    resolver: Callable[[str], str],
    anchor_map: dict[str, str],
    *,
    load_import: Callable[[str], str] | None = None,
    report: Callable[[str], None] | None = None,
    budget: StyleBudget | None = None,
) -> str:
    budget = budget if budget is not None else StyleBudget()
    budget.reserve(rules=1)
    if len(css.encode('utf-8')) > MAX_STYLE_BYTES:
        raise StyleLimit('源样式超过大小限制。')
    root = f':where([data-source-scope="{scope}"])'

    def rules(source, depth=0):
        if depth > MAX_STYLE_DEPTH:
            raise StyleLimit('样式嵌套超过深度限制。')
        result = []
        def append(value):
            budget.reserve(size=len(value.encode('utf-8')))
            result.append(value)
        for rule in tinycss2.parse_rule_list(source, skip_comments=True, skip_whitespace=True):
            budget.reserve(rules=1)
            if rule.type == "qualified-rule":
                def rename(tokens):
                    rewritten = []
                    for index, token in enumerate(tokens):
                        if token.type == "hash" and token.is_identifier and token.value in anchor_map:
                            canonical = anchor_map[token.value]
                            # :is retains ID specificity while its attribute branch also
                            # matches reopened ancestors whose link ID must be removed.
                            rewritten.extend(tinycss2.parse_component_value_list(f':is(#{canonical}, [data-source-element-id="{canonical}"])'))
                            continue
                        elif token.type == "function" and token.lower_name in {"is", "where", "not", "has"}:
                            token.arguments = rename(token.arguments)
                        elif token.type == "ident":
                            previous = tokens[index - 1] if index else None
                            if token.lower_value == "root" and previous is not None and previous.type == "literal" and previous.value == ":":
                                rewritten.pop()
                                rewritten.extend(tinycss2.parse_component_value_list('[data-source-html]'))
                                continue
                            if token.lower_value in {"html", "body"} and (
                                previous is None or previous.type == "whitespace" or previous.type == "literal" and previous.value in {",", ">", "+", "~"}
                            ):
                                kind = token.lower_value
                                rewritten.extend(tinycss2.parse_component_value_list(f'any-to-md-{kind}:where([data-source-{kind}])'))
                                continue
                        elif token.type == "[] block":
                            attribute = next((part for part in token.content if part.type != "whitespace"), None)
                            if attribute is not None and attribute.type == "ident" and attribute.lower_value == "id":
                                attribute.value = "data-source-original-id"
                        rewritten.append(token)
                    return rewritten
                rule.prelude = rename(rule.prelude)
                groups = [[]]
                for token in rule.prelude:
                    if token.type == "literal" and token.value == ",":
                        groups.append([])
                    else:
                        groups[-1].append(token)
                selectors = []
                for group in groups:
                    selector = tinycss2.serialize(group).strip()
                    if not selector:
                        continue
                    selectors.append(f"{root} {selector}")
                _urls(rule.content, resolver)
                append(", ".join(selectors) + "{" + tinycss2.serialize(rule.content) + "}")
            elif rule.type == "at-rule" and rule.lower_at_keyword == "import":
                try:
                    target, layer, supports, media = _import_parts(rule.prelude)
                    if load_import is None:
                        raise ValueError("未配置样式导入解析")
                    content = load_import(target)
                    if media:
                        content = "@media " + media + "{" + content + "}"
                    if supports is not None:
                        content = "@supports " + supports + "{" + content + "}"
                    if layer is not None:
                        content = "@layer " + layer + "{" + content + "}"
                    append(content)
                except StyleLimit:
                    raise
                except ValueError as exc:
                    if report is not None:
                        report(str(exc))
            elif rule.type == "at-rule" and rule.content is not None:
                if rule.lower_at_keyword in {"media", "supports", "layer"}:
                    content = rules(rule.content, depth+1)
                elif rule.lower_at_keyword == "font-face":
                    _urls(rule.content, resolver)
                    content = tinycss2.serialize(rule.content)
                else:
                    if report is not None:
                        report(f"未保留源 @{rule.at_keyword} 规则，避免影响其他文档或生成错误样式。")
                    continue
                append("@" + rule.at_keyword + " " + tinycss2.serialize(rule.prelude) + "{" + content + "}")
        return "\n".join(result)

    return rules(css)
