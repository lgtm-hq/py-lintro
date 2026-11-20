"""Parser messages osv-scanner 2.7.1 printed for deliberately broken files.

Each entry was captured by running ``osv-scanner scan source --verbosity
error -r .`` (osv-scanner 2.7.1, osv-scalibr 0.5.3) on a directory holding one
broken file (#3553). ``<file>`` stands for that file's path as osv-scanner
printed it; everything else is verbatim. The last field names the template in
``lintro.tools.osv_scanner.extraction_log.PARSER_MESSAGE_TEMPLATES`` the
message must match.
"""

from __future__ import annotations

from typing import NamedTuple


class CapturedMessage(NamedTuple):
    """One captured error line, from after the file's path.

    Attributes:
        plugin: The extractor osv-scanner named in the header.
        file_name: The broken file's name.
        error: The text after ``<file>: ``, with ``<file>`` for the path.
        template: The template it must match.
    """

    plugin: str
    file_name: str
    error: str
    template: str


_LOCK = ("javascript/packagelockjson", "package-lock.json")
_PNPM = ("javascript/pnpmlock", "pnpm-lock.yaml")
_POETRY = ("python/poetrylock", "poetry.lock")
_POM = ("java/pomxml", "pom.xml")
_GOMOD = ("go/gomod", "go.mod")

#: Every captured message, by the fixture that produced it.
CAPTURED: dict[str, CapturedMessage] = {
    "json-value": CapturedMessage(
        *_LOCK,
        "could not extract: invalid character 'b' looking for beginning of value",
        "json/value",
    ),
    "json-comment": CapturedMessage(
        *_LOCK,
        "could not extract: invalid character '/' looking for beginning of "
        "object key string",
        "json/key-string",
    ),
    "json-key": CapturedMessage(
        *_LOCK,
        "could not extract: invalid character '1' after object key",
        "json/after-key",
    ),
    "json-pair": CapturedMessage(
        *_LOCK,
        "could not extract: invalid character '\"' after object key:value pair",
        "json/after-pair",
    ),
    "json-array": CapturedMessage(
        *_LOCK,
        "could not extract: invalid character '2' after array element",
        "json/after-element",
    ),
    "json-top": CapturedMessage(
        *_LOCK,
        "could not extract: invalid character '}' after top-level value",
        "json/after-top",
    ),
    "json-number": CapturedMessage(
        *_LOCK,
        "could not extract: invalid character '}' in numeric literal",
        "json/numeric",
    ),
    "json-null": CapturedMessage(
        *_LOCK,
        "could not extract: invalid character '}' in literal null (expecting 'l')",
        "json/literal",
    ),
    "json-false": CapturedMessage(
        *_LOCK,
        "could not extract: invalid character '}' in literal false (expecting 'e')",
        "json/literal",
    ),
    "json-control": CapturedMessage(
        *_LOCK,
        "could not extract: invalid character '\\x01' in string",
        "json/string",
    ),
    "json-newline": CapturedMessage(
        *_LOCK,
        "could not extract: invalid character '\\n' in string",
        "json/string",
    ),
    "json-escape": CapturedMessage(
        *_LOCK,
        "could not extract: invalid escape sequence `\\u12z4` in string",
        "json/escape",
    ),
    "json-escape-short": CapturedMessage(
        *_LOCK,
        'could not extract: invalid escape sequence `\\u12"}` in string',
        "json/escape",
    ),
    "json-end": CapturedMessage(
        "php/composerlock",
        "composer.lock",
        "could not extract: unexpected end of JSON input",
        "json/end",
    ),
    "json-struct-field": CapturedMessage(
        *_LOCK,
        "could not extract: json: cannot unmarshal number into Go struct field "
        ".packages.node_modules~1x.version of type string",
        "json/struct-field",
    ),
    "json-map-type": CapturedMessage(
        "python/pipfilelock",
        "Pipfile.lock",
        "could not extract: json: cannot unmarshal number into Go struct field "
        "pipenvLockFile.default of type map[string]pipfilelock.pipenvPackage",
        "json/struct-field",
    ),
    "bun-no-colon": CapturedMessage(
        "javascript/bunlock",
        "bun.lock",
        "could not extract json: cannot unmarshal number into Go struct field "
        ".packages.a of type []interface {}",
        "json/struct-field",
    ),
    "json-value-type": CapturedMessage(
        *_LOCK,
        "could not extract: json: cannot unmarshal string into Go value of type "
        "packagelockjson.LockFile",
        "json/value-type",
    ),
    "yaml-seq": CapturedMessage(
        *_PNPM,
        "could not extract: yaml: line 1: did not find expected ',' or ']'",
        "yaml/expected-or",
    ),
    "pubspec-map": CapturedMessage(
        "dart/pubspec",
        "pubspec.lock",
        "could not extract: yaml: line 1: did not find expected ',' or ']'",
        "yaml/expected-or",
    ),
    "yaml-map": CapturedMessage(
        *_PNPM,
        "could not extract: yaml: line 1: did not find expected ',' or '}'",
        "yaml/expected-or",
    ),
    "yaml-indicator": CapturedMessage(
        *_PNPM,
        "could not extract: yaml: line 1: did not find expected '-' indicator",
        "yaml/expected-indicator",
    ),
    "yaml-key": CapturedMessage(
        *_PNPM,
        "could not extract: yaml: line 2: did not find expected key",
        "yaml/expected-key",
    ),
    "yaml-mapping": CapturedMessage(
        *_PNPM,
        "could not extract: yaml: mapping values are not allowed in this context",
        "yaml/mapping",
    ),
    "yaml-character": CapturedMessage(
        *_PNPM,
        "could not extract: yaml: line 2: found character that cannot start any "
        "token",
        "yaml/character",
    ),
    "yaml-end": CapturedMessage(
        *_PNPM,
        "could not extract: yaml: line 2: found unexpected end of stream",
        "yaml/end",
    ),
    "yaml-anchor": CapturedMessage(
        *_PNPM,
        "could not extract: yaml: unknown anchor 'y' referenced",
        "yaml/anchor",
    ),
    "toml-newline": CapturedMessage(
        *_POETRY,
        'could not extract: toml: line 1 (last key "a"): strings cannot contain '
        "newlines",
        "toml/newline",
    ),
    "toml-defined": CapturedMessage(
        *_POETRY,
        "could not extract: toml: line 2 (last key \"a\"): Key 'a' has already "
        "been defined.",
        "toml/defined",
    ),
    "toml-dot": CapturedMessage(
        *_POETRY,
        "could not extract: toml: line 1: expected '.' or '=', but got 'b' instead",
        "toml/expected-or",
    ),
    "toml-table-name": CapturedMessage(
        *_POETRY,
        "could not extract: toml: line 2: expected '.' or ']' to end table name, "
        "but got '\\n' instead",
        "toml/table-name",
    ),
    "toml-types": CapturedMessage(
        *_POETRY,
        'could not extract: toml: line 2 (last key "package.name"): incompatible '
        "types: TOML value has type int64; destination has type string",
        "toml/types",
    ),
    "toml-top-level": CapturedMessage(
        *_POETRY,
        "could not extract: toml: line 1: expected a top-level item to end with "
        "a newline, comment, or EOF, but got 'X' instead",
        "toml/top-level",
    ),
    "toml-eof": CapturedMessage(
        *_POETRY,
        'could not extract: toml: line 1 (last key "a"): unexpected EOF; '
        "expected value",
        "toml/eof-value",
    ),
    "toml-value": CapturedMessage(
        *_POETRY,
        'could not extract: toml: line 1 (last key "not"): expected value but '
        'found "toml" instead',
        "toml/expected-value",
    ),
    "uv-blank": CapturedMessage(
        "python/uvlock",
        "uv.lock",
        "could not extract: toml: line 1: unexpected '=': key name appears blank",
        "toml/blank-key",
    ),
    "toml-inline": CapturedMessage(
        *_POETRY,
        'could not extract: toml: line 1 (last key "a.b"): expected a comma or '
        "an inline table terminator '}', but got end of file instead",
        "toml/inline-table",
    ),
    "toml-array": CapturedMessage(
        *_POETRY,
        "could not extract: toml: line 1 (last key \"a\"): expected a comma (',') "
        "or array terminator (']'), but got end of file",
        "toml/array",
    ),
    "toml-escape": CapturedMessage(
        *_POETRY,
        'could not extract: toml: line 1 (last key "a"): invalid escape in '
        "string '\\q'",
        "toml/escape",
    ),
    "cargo-escape": CapturedMessage(
        "rust/cargolock",
        "Cargo.lock",
        'could not extract: toml: line 1 (last key "a"): invalid escape in '
        "string '\\q'",
        "toml/escape",
    ),
    "pom-eof": CapturedMessage(
        *_POM,
        "could not extract pom from <file>: XML syntax error on line 1: "
        "unexpected EOF",
        "xml/eof",
    ),
    "packages-config-eof": CapturedMessage(
        "dotnet/packagesconfig",
        "packages.config",
        "XML syntax error on line 1: unexpected EOF",
        "xml/eof",
    ),
    "pom-closed": CapturedMessage(
        *_POM,
        "could not extract pom from <file>: XML syntax error on line 1: element "
        "<project> closed by </foo>",
        "xml/closed",
    ),
    "pom-entity": CapturedMessage(
        *_POM,
        "could not extract pom from <file>: XML syntax error on line 1: invalid "
        "character entity &bogus;",
        "xml/entity",
    ),
    "pom-entity-semicolon": CapturedMessage(
        *_POM,
        "could not extract pom from <file>: XML syntax error on line 1: invalid "
        "character entity &#x (no semicolon)",
        "xml/entity-semicolon",
    ),
    "pom-element-name": CapturedMessage(
        *_POM,
        "could not extract pom from <file>: XML syntax error on line 1: expected "
        "element name after </",
        "xml/element-name",
    ),
    "pom-name": CapturedMessage(
        *_POM,
        "could not extract pom from <file>: XML syntax error on line 1: invalid "
        "XML name: 1",
        "xml/name",
    ),
    "pom-attribute": CapturedMessage(
        *_POM,
        "could not extract pom from <file>: XML syntax error on line 1: "
        "attribute name without = in element",
        "xml/attribute",
    ),
    "packages-config-attribute": CapturedMessage(
        "dotnet/packagesconfig",
        "packages.config",
        "XML syntax error on line 1: attribute name without = in element",
        "xml/attribute",
    ),
    "pom-utf8": CapturedMessage(
        *_POM,
        "could not extract pom from <file>: XML syntax error on line 1: invalid "
        "UTF-8",
        "xml/utf8",
    ),
    "gomod-directive": CapturedMessage(
        *_GOMOD,
        "could not extract: <file>:2: unknown directive: foo",
        "gomod/directive",
    ),
    "gomod-repeated": CapturedMessage(
        *_GOMOD,
        "could not extract: <file>:2: repeated module statement",
        "gomod/repeated",
    ),
    "gomod-newline": CapturedMessage(
        *_GOMOD,
        "could not extract: <file>:1:10: unexpected newline in string",
        "gomod/newline",
    ),
    "gomod-usage-module": CapturedMessage(
        *_GOMOD,
        "could not extract: <file>:1: usage: module module/path",
        "gomod/usage-module",
    ),
    "gomod-usage-require": CapturedMessage(
        *_GOMOD,
        "could not extract: <file>:2: usage: require module/path v1.2.3",
        "gomod/usage-version",
    ),
    "gomod-usage-exclude": CapturedMessage(
        *_GOMOD,
        "could not extract: <file>:2: usage: exclude module/path v1.2.3",
        "gomod/usage-version",
    ),
    "gomod-godebug": CapturedMessage(
        *_GOMOD,
        "could not extract: <file>:3: usage: godebug key=value",
        "gomod/usage-godebug",
    ),
    "gomod-go-version": CapturedMessage(
        *_GOMOD,
        "could not extract: <file>:2: invalid go version 'abc': must match "
        "format 1.23.0",
        "gomod/go-version",
    ),
    "gomod-toolchain": CapturedMessage(
        *_GOMOD,
        "could not extract: <file>:2: invalid toolchain version 'bad': must "
        "match format go1.23.0 or default",
        "gomod/toolchain",
    ),
    "gomod-require-version": CapturedMessage(
        *_GOMOD,
        'could not extract: <file>:2: require a/b: version "vX" invalid: must '
        "be of the form v1.2.3",
        "gomod/require-version",
    ),
    "gomod-block": CapturedMessage(
        *_GOMOD,
        "could not extract: <file>:3: syntax error (unterminated block started "
        "at <file>:2:1)",
        "gomod/block",
    ),
    "pom-empty": CapturedMessage(
        *_POM,
        "could not extract pom from <file>: EOF",
        "eof/pom",
    ),
    "conan-eof": CapturedMessage(
        "cpp/conanlock",
        "conan.lock",
        "could not extract: unexpected EOF",
        "eof/unexpected",
    ),
    "renv-eof": CapturedMessage(
        "r/renvlock",
        "renv.lock",
        "could not extract: unexpected EOF",
        "eof/unexpected",
    ),
    "packages-lock-eof": CapturedMessage(
        "dotnet/packageslockjson",
        "packages.lock.json",
        "failed to decode packages.lock.json file: unexpected EOF",
        "eof/unexpected",
    ),
    "unreadable": CapturedMessage(
        *_LOCK,
        "Open(<file>): open <file>: permission denied",
        "open/permission",
    ),
}
