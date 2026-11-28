from __future__ import annotations

import re

import nh3
import tinycss2

MAX_HOMEPAGE_CONTENT_LENGTH = 8000
MAX_HOMEPAGE_CSS_LENGTH = 8000

_CSS_SELECTOR_RE = re.compile(r"""[A-Za-z0-9_.*#:\s,\[\]="'|~^$+>-]+""")
_KEYFRAME_SELECTOR_RE = re.compile(r"(?:from|to|\d+(?:\.\d+)?%)(?:\s*,\s*(?:from|to|\d+(?:\.\d+)?%))*")
_CSS_PROPERTY_RE = re.compile(r"(?:--)?[a-zA-Z_][a-zA-Z0-9_-]*")
_BLOCKED_CSS_PROPERTIES = {"behavior", "-moz-binding", "-ms-behavior", "src"}
_BLOCKED_CSS_FUNCTIONS = {
    "cross-fade",
    "element",
    "expression",
    "image",
    "image-set",
    "paint",
    "src",
    "url",
    "-moz-element",
    "-webkit-image-set",
}

_HOMEPAGE_CLEANER = nh3.Cleaner(
    tags={
        "a",
        "article",
        "b",
        "blockquote",
        "br",
        "div",
        "em",
        "figcaption",
        "figure",
        "h2",
        "h3",
        "h4",
        "hr",
        "i",
        "img",
        "li",
        "ol",
        "p",
        "section",
        "span",
        "strong",
        "ul",
    },
    clean_content_tags={"embed", "iframe", "math", "object", "script", "style", "svg", "template"},
    attributes={
        "*": {"class"},
        "a": {"href", "title", "class"},
        "img": {"alt", "src", "class"},
    },
    url_schemes={"http", "https", "mailto"},
    url_relative="pass_through",
    link_rel="noopener noreferrer",
)
_ESCAPED_CLASS_QUOTES_RE = re.compile(
    r'(?P<prefix>(?<![\w:-])class\s*=\s*)(?P<quote>\\*(?:&quot;|"))'
    r'(?P<value>.*?)(?P=quote)(?=\s|/?>|$)',
    re.IGNORECASE | re.DOTALL,
)


def sanitize_homepage_html(raw_html: str) -> str:
    if not isinstance(raw_html, str):
        raise ValueError("Homepage content must be HTML text.")
    if len(raw_html) > MAX_HOMEPAGE_CONTENT_LENGTH:
        raise ValueError(f"Homepage content must be {MAX_HOMEPAGE_CONTENT_LENGTH} characters or fewer.")

    normalized_html = _ESCAPED_CLASS_QUOTES_RE.sub(
        lambda match: f'{match["prefix"]}"{match["value"]}"',
        raw_html,
    )
    sanitized = _HOMEPAGE_CLEANER.clean(normalized_html.strip())
    if not sanitized.strip():
        raise ValueError("Add some visible content before saving this draft.")
    return sanitized


def _validate_css_tokens(tokens: list[object]) -> None:
    for token in tokens:
        token_type = getattr(token, "type", "")
        if token_type in {"error", "url", "bad-url", "at-keyword"}:
            raise ValueError("CSS errors, URLs, and embedded resources are not allowed.")
        if token_type == "function":
            function_name = token.lower_name
            if function_name in _BLOCKED_CSS_FUNCTIONS:
                raise ValueError("CSS functions that load or execute external content are not allowed.")
            _validate_css_tokens(token.arguments)
        elif token_type in {"() block", "[] block", "{} block"}:
            _validate_css_tokens(token.content)


def _sanitize_css_declarations(tokens: list[object]) -> str:
    declarations = tinycss2.parse_declaration_list(
        tokens,
        skip_comments=True,
        skip_whitespace=True,
    )
    sanitized = []
    for declaration in declarations:
        if declaration.type != "declaration":
            raise ValueError("CSS contains an invalid declaration.")
        property_name = declaration.lower_name
        if (
            not _CSS_PROPERTY_RE.fullmatch(property_name)
            or property_name in _BLOCKED_CSS_PROPERTIES
        ):
            raise ValueError(f"CSS property '{property_name}' is not allowed.")
        _validate_css_tokens(declaration.value)
        value = tinycss2.serialize(declaration.value).strip()
        if not value:
            raise ValueError(f"CSS property '{property_name}' needs a value.")
        important = " !important" if declaration.important else ""
        sanitized.append(f"{property_name}: {value}{important}")
    return "; ".join(sanitized)


def _sanitize_css_rules(rules: list[object], keyframes: bool = False) -> list[str]:
    sanitized = []
    for rule in rules:
        if rule.type == "qualified-rule":
            selector = tinycss2.serialize(rule.prelude).strip()
            selector_pattern = _KEYFRAME_SELECTOR_RE if keyframes else _CSS_SELECTOR_RE
            if not selector or not selector_pattern.fullmatch(selector):
                raise ValueError("CSS contains an invalid selector.")
            declarations = _sanitize_css_declarations(rule.content)
            if declarations:
                sanitized.append(f"{selector} {{ {declarations}; }}")
            continue

        if rule.type != "at-rule" or rule.content is None:
            raise ValueError("CSS contains an unsupported rule.")

        at_rule = rule.lower_at_keyword
        prelude = tinycss2.serialize(rule.prelude).strip()
        _validate_css_tokens(rule.prelude)
        if at_rule in {"media", "supports"} and prelude:
            nested_rules = tinycss2.parse_rule_list(
                rule.content,
                skip_comments=True,
                skip_whitespace=True,
            )
            nested_css = _sanitize_css_rules(nested_rules)
            if nested_css:
                sanitized.append(f"@{at_rule} {prelude} {{ {' '.join(nested_css)} }}")
            continue

        if at_rule in {"keyframes", "-webkit-keyframes"} and re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_-]*",
            prelude,
        ):
            nested_rules = tinycss2.parse_rule_list(
                rule.content,
                skip_comments=True,
                skip_whitespace=True,
            )
            nested_css = _sanitize_css_rules(nested_rules, keyframes=True)
            if nested_css:
                sanitized.append(f"@{at_rule} {prelude} {{ {' '.join(nested_css)} }}")
            continue

        raise ValueError(f"CSS at-rule '@{at_rule}' is not allowed.")
    return sanitized


def sanitize_homepage_css(raw_css: str) -> str:
    if not isinstance(raw_css, str):
        raise ValueError("Homepage CSS must be text.")
    if len(raw_css) > MAX_HOMEPAGE_CSS_LENGTH:
        raise ValueError(f"Homepage CSS must be {MAX_HOMEPAGE_CSS_LENGTH} characters or fewer.")
    if not raw_css.strip():
        return ""
    if "<" in raw_css:
        raise ValueError("Homepage CSS cannot contain HTML markup.")

    rules = tinycss2.parse_stylesheet(
        raw_css,
        skip_comments=True,
        skip_whitespace=True,
    )
    if any(rule.type == "error" for rule in rules):
        raise ValueError("Homepage CSS contains a syntax error.")
    return "\n".join(_sanitize_css_rules(rules))
