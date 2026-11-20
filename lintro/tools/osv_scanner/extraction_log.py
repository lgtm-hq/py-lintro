"""Read what osv-scanner prints on standard error at ``--verbosity error``.

lintro runs osv-scanner with ``--verbosity error``, so a scan that went well
prints nothing on standard error. Anything it does print is an error, and the
run is incomplete, with one exception: a **resolution note**. That is the
optional transitive-dependency enricher reporting that the registry has no
such package or version, or that no version satisfies the requirements, on
one line::

    Error during extraction: (extracting as transitivedependency/requirements)
    failed resolution for <path>: package System(7):<name>: not found

The enricher is ``transitivedependency/requirements`` or
``transitivedependency/pomxml``, ``<path>`` names one existing regular file,
and the whole text after it is one of :data:`HARMLESS_RESOLUTION_ENDINGS`. The
manifest's listed packages were scanned; only their indirect dependencies were
not resolved. A transport failure (``rpc error``, ``max retries exhausted``, a
timeout) or any text not listed is an error.

Standard error is read as messages, not lines. A message starts with an
``Error during extraction`` header and continues on the lines osv-scanner
prints under it: tab-indented ``<path>: <error>`` lines when one extractor
failed on several files, further ``failed resolution for`` messages joined to
a transitive one, and the cause lines of ``resolution impossible:``. That
error ends in a newline of its own, so osv-scanner prints one blank line after
its last cause; after that blank line only a further ``failed resolution for``
continues the message. Each part of a message is judged on its own; a message
with any part that is neither a resolution note nor an error for a file lintro
excludes is kept whole as error text. Every line outside such a message is
error text too.

A part is dropped as an error for an excluded file only when it starts with
one existing regular file under an excluded path and the whole text after that
file is a recognised shape: a parser or permission error for that same file
(:data:`EXCLUDED_FILE_ERRORS`), or, for a ``failed resolution for`` part, one of
:data:`HARMLESS_RESOLUTION_ENDINGS`. Any other text keeps the error, so one
that names a second file is never dropped. Checked against osv-scanner 2.4.0
and 2.7.1 (``pkg/osvscanner/scan.go``, ``logPluginStatus``; osv-scalibr
``enricher/transitivedependency``; deps.dev ``util/resolve``).
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from lintro.tools.core.incomplete_run import tool_message_excerpt

#: The first line osv-scanner prints for a plugin that did not succeed.
_HEADER_RE = re.compile(
    r"^Error during extraction: \((?:extracting as )?(?P<plugin>[^)]*)\) ?"
    r"(?P<detail>.*)$",
)

#: Enrichers that resolve indirect dependencies of an already-read manifest.
TRANSITIVE_PLUGINS: frozenset[str] = frozenset(
    {"transitivedependency/requirements", "transitivedependency/pomxml"},
)

#: How each transitive enricher names a manifest it could not resolve.
_RESOLUTION_PREFIX: str = "failed resolution for "

#: The first line of the pip-style resolver's "unsatisfiable" error.
_IMPOSSIBLE: str = "resolution impossible:"

#: How each cause line under ``resolution impossible:`` starts.
_CAUSE_PREFIXES: tuple[str, ...] = (
    "no candidates at all for: ",
    "requirements conflict: ",
)

#: What osv-scanner prints, alone, when it found nothing to scan: the whole
#: line, the same at 2.4.0 and 2.7.1 (``cmd/osv-scanner/internal/cmd/run.go``).
NO_SOURCES_LINE: str = "No package sources found, --help for usage information."

_NAME = r"[A-Za-z0-9._-]+"
#: deps.dev prints PyPI as ``System(7)`` and Maven as ``Maven``.
_SYSTEM = r"(?:[A-Za-z]+|System\(\d+\))"
_PACKAGE_KEY = rf"{_SYSTEM}:{_NAME}(?::{_NAME})?"
_VERSION = r"[A-Za-z0-9._+~!-]+"
_VERSION_KEY = rf"{_PACKAGE_KEY}\[[A-Za-z]+:{_VERSION}\]"
#: deps.dev's ``ErrNotFound``, returned only for a gRPC ``NotFound`` reply.
_NOT_FOUND = rf"(?:package {_PACKAGE_KEY}|version {_VERSION_KEY}): not found"
_MAVEN_ROOT = rf"\{{Maven:{_NAME}:{_NAME}\[Concrete:{_VERSION}\] \{{\}}\}}"
#: One requirement specifier as deps.dev's PyPI resolver prints it: an
#: operator and a version, with the spaces the manifest had kept (2.4.0 prints
#: ``">= 99999 , < 100000"``). ``===`` (arbitrary equality) takes any version
#: text without spaces, commas, quotes or backslashes.
_SPECIFIER = r' *(?:===[^\s",\\]+|(?:~=|==|!=|<=|>=|<|>) *[A-Za-z0-9._+*!-]+) *'
#: ``%q`` of the requirements joined with ``,`` (``requirementsConflictedError``).
_SPECIFIERS = rf'"{_SPECIFIER}(?:,{_SPECIFIER})*"'
_CAUSE = (
    rf"(?:no candidates at all for: {_NAME}|requirements conflict: {_NAME}:) "
    rf"{_SPECIFIERS}"
)

# Rule for this list (#3559): an ending belongs here only when it is provably a
# property of the project's own files and osv-scanner cannot print it after a
# network or registry failure. Any other error stays an incomplete run. Adding
# an ending takes an explicit maintainer decision recorded in an issue, with
# source evidence that a transport failure cannot produce it. See "The
# allow-list and its limit" in docs/configuration.md. Examined and rejected:
# Maven's ``failed parsing version constraint '...[Requirement:]': invalid
# empty constraint``. deps.dev drops a failed BOM import
# (util/maven/dependency.go), so osv-scanner prints the same text when Maven
# Central is unreachable.
#: The only endings after ``failed resolution for <path>: `` that are
#: harmless, each matched against the whole remaining message:
#:
#: * ``package System(7):<name>: not found`` and
#:   ``version <key>[Concrete:<v>]: not found``: the registry answered that it
#:   has no such package or version (a private package);
#: * ``failed resolving {Maven:<g>:<a>[Concrete:<v>] {}}: <the same>``: the
#:   Maven enricher wrapping that answer;
#: * ``resolution impossible:`` followed by one or more
#:   ``no candidates at all for: <name> "<spec>"`` or
#:   ``requirements conflict: <name>: "<specs>"`` lines: the registry answered
#:   and no version satisfies the requirements. deps.dev raises this only from
#:   versions it received; a lookup that failed in transport is returned as
#:   its own error instead.
HARMLESS_RESOLUTION_ENDINGS: tuple[re.Pattern[str], ...] = (
    re.compile(_NOT_FOUND),
    re.compile(rf"failed resolving {_MAVEN_ROOT}: {_NOT_FOUND}"),
    re.compile(rf"{re.escape(_IMPOSSIBLE)}(?:\n{_CAUSE})+"),
)

#: The short form of a resolution note, for a summary table's Notes column.
RESOLUTION_NOTE_SUMMARY: str = "indirect dependencies not resolved"


@dataclass(frozen=True)
class ResolutionNote:
    """One manifest whose indirect dependencies were not resolved.

    Attributes:
        source: Absolute path of the manifest.
        printed_path: The path as osv-scanner printed it.
        text: The message after ``failed resolution for ``, path included.
    """

    source: str
    printed_path: str
    text: str


@dataclass(frozen=True)
class StderrReport:
    """What osv-scanner's standard error says about one run.

    Attributes:
        notes: The resolution notes, outside excluded paths.
        errors: Every other line osv-scanner printed, verbatim and in order,
            except blank lines and messages wholly about excluded files.
        excluded: How many messages were dropped because every file they
            name is excluded.
        printed: Every non-blank line osv-scanner printed, verbatim, for
            quoting when the run is incomplete.
    """

    notes: tuple[ResolutionNote, ...] = ()
    errors: tuple[str, ...] = ()
    excluded: int = 0
    printed: tuple[str, ...] = ()

    @property
    def explained(self) -> bool:
        """Return whether osv-scanner logged only notes or excluded errors.

        Returns:
            True when it logged something and all of it is accounted for.
        """
        return not self.errors and bool(self.notes or self.excluded)

    @property
    def only_no_sources(self) -> bool:
        """Return whether the one error is "No package sources found".

        Returns:
            True when that line is all osv-scanner printed besides notes and
            excluded errors.
        """
        return self.errors == (NO_SOURCES_LINE,)


def _named_file(path_text: str, scan_root: Path) -> tuple[str, str] | None:
    """Return the regular file a printed path names, if exactly one.

    Args:
        path_text: The path as osv-scanner printed it, usually without its
            leading separator.
        scan_root: Directory osv-scanner ran in.

    Returns:
        The absolute path and the printed form, or None.
    """
    if not path_text.strip():
        return None
    files = {
        str(path)
        for path in (Path(os.sep + path_text.lstrip(os.sep)), scan_root / path_text)
        if os.path.isfile(path)
    }
    if len({os.path.realpath(path) for path in files}) != 1:
        return None
    absolute = os.sep + path_text.lstrip(os.sep)
    return (absolute if absolute in files else files.pop()), path_text


def source_of(text: str, scan_root: Path) -> tuple[str, str, str] | None:
    """Return the one regular file at the start of ``<path>: <error>``.

    The path may itself contain ``": "``, so every split point on the first
    line is tried. A split that names a directory, or nothing, is not a
    source; when several splits name different files the part is ambiguous.

    Args:
        text: The part, starting with the path.
        scan_root: Directory osv-scanner ran in.

    Returns:
        The absolute path, the printed form and the error after the path, or
        None when no split, or more than one, names an existing regular file.
    """
    first_line = text.split("\n", 1)[0]
    found: dict[str, tuple[str, str, str]] = {}
    start = 0
    while (index := first_line.find(": ", start)) >= 0:
        named = _named_file(first_line[:index], scan_root)
        if named is not None:
            found[os.path.realpath(named[0])] = (*named, text[index + 2 :])
        start = index + 1
    if len(found) != 1:
        return None
    return next(iter(found.values()))


#: Stands for the excluded file's own path in an error about it.
_SELF = "\x00"

# Every pattern below was derived from messages osv-scanner 2.7.1 printed for
# deliberately broken fixtures (#3553); the comments quote them, with <file>
# for the file's own path. The text after a parser prefix must itself be a
# parser message of that family. Any other text keeps the error, so the run
# is incomplete.

#: One word of a parser message: a plain word or number, or a short quoted
#: token (``','``, ``'\n'``, ``"toml"``), with its trailing punctuation. A path
#: or a dotted file name is not a word.
_QUOTED = (
    r"(?:'(?:[^'\s/\\]|\\(?:[abfnrtv\\']|x[0-9a-f]{2}|u[0-9a-f]{4}"
    r"|U[0-9a-f]{8})){1,40}'"
    r'|"[^"\s/\\]{1,40}")'
)
_WORD = rf"(?:[A-Za-z][A-Za-z0-9_-]*|\d+|{_QUOTED})[.,;:]?"
#: A whole message: up to 25 words, the last not ending in ``:``, because a
#: message that ends there (``yaml: unmarshal errors:``) goes on below.
_PHRASE = rf"(?:{_WORD} ){{0,24}}(?:[A-Za-z][A-Za-z0-9_-]*|\d+|{_QUOTED})[.,;]?"
#: An XML message may also name an element (``<project>``, ``</foo>``), an
#: entity (``&bogus;``) or a bare ``<`` or ``=``.
_XML_WORD = rf"(?:{_WORD}|</?[A-Za-z_][\w.:-]{{0,60}}>|&[A-Za-z0-9#]{{1,40}};|[<=])"

#: Go's ``encoding/json`` (package-lock.json, composer.lock, Pipfile.lock,
#: bun.lock). Captured: ``invalid character '}' looking for beginning of
#: value``, ``invalid character '/' looking for beginning of object key
#: string``, ``invalid character '1' after object key``, ``invalid character
#: '"' after object key:value pair``, ``invalid character '2' after array
#: element``, ``invalid character 'x' after top-level value``, ``invalid
#: character '}' in numeric literal``, ``invalid character '}' in literal true
#: (expecting 'e')``, ``invalid character '\x01' in string``, ``invalid escape
#: sequence `\q` in string``, ``unexpected end of JSON input``, ``json: cannot
#: unmarshal number into Go struct field .packages.node_modules~1x.version of
#: type string``, ``json: cannot unmarshal string into Go value of type
#: packagelockjson.LockFile``, ``json: cannot unmarshal number into Go struct
#: field pipenvLockFile.default of type map[string]pipfilelock.pipenvPackage``
#: and ``... of type []interface {}``.
_JSON_CONTEXT = (
    r"(?:looking for beginning of (?:value|object key string)"
    r"|after (?:object key(?::value pair)?|array element|top-level value)"
    r"|in (?:string(?: literal)?|string escape code"
    r"|\\u hexadecimal character escape"
    r"|numeric literal|exponent of numeric literal"
    r"|literal (?:true|false|null) \(expecting '[a-z]'\))"
    r"|after decimal point in numeric literal)"
)
_JSON_CHAR = (
    r"'(?:\\'|[^'\\]|\\[abfnrtv\\]|\\x[0-9a-f]{2}|\\u[0-9a-f]{4}|\\U[0-9a-f]{8})'"
)
_GO_TYPE = (
    r"(?:\[\d*\]|map\[[A-Za-z_][\w.]{0,80}\]|\*){0,4}"
    r"(?:interface \{\}|[A-Za-z_][\w.]{0,120})"
)
_JSON_ERROR = (
    rf"(?:invalid character {_JSON_CHAR} {_JSON_CONTEXT}"
    r"|unexpected end of JSON input"
    r"|invalid escape sequence `\\[^`\s/]{1,10}` in string"
    r"|json: cannot unmarshal [a-z]+ into Go "
    rf"(?:struct field [^\s/\\:]{{1,200}} |value )of type {_GO_TYPE})"
)
#: go-yaml (pnpm-lock.yaml, pubspec.lock). Captured: ``yaml: line 1: did not
#: find expected ',' or ']'``, ``... ',' or '}'``, ``yaml: line 1: did not find
#: expected '-' indicator``, ``yaml: line 2: mapping values are not allowed in
#: this context`` (also without ``line <n>:``), ``yaml: line 2: found character
#: that cannot start any token``, ``yaml: line 2: found unexpected end of
#: stream``, ``yaml: unknown anchor 'y' referenced``. ``yaml: unmarshal
#: errors:`` continues on an indented line, which is read as a separate error
#: and keeps the run incomplete (a documented limit).
_YAML_ERROR = rf"yaml: (?:line \d+: )?{_PHRASE}"
#: BurntSushi/toml (poetry.lock, uv.lock, Cargo.lock). Captured: ``toml: line 1
#: (last key "a"): strings cannot contain newlines``, ``toml: line 2 (last key
#: "a"): Key 'a' has already been defined.``, ``toml: line 1: expected '.' or
#: '=', but got 't' instead``, ``toml: line 2 (last key "package.name"):
#: incompatible types: TOML value has type int64; destination has type
#: string``, ``toml: line 1: expected a top-level item to end with a newline,
#: comment, or EOF, but got 'X' instead``, ``toml: line 1 (last key "a"):
#: unexpected EOF; expected value``, ``toml: line 2: expected '.' or ']' to end
#: table name, but got '\n' instead``, ``toml: line 1: unexpected '=': key name
#: appears blank``, ``toml: line 1 (last key "a.b"): expected a comma or an
#: inline table terminator '}', but got end of file instead``.
_TOML_ERROR = rf'toml: line \d+(?: \(last key "[^"\s/\\]{{1,200}}"\))?: {_PHRASE}'
#: Go's ``encoding/xml`` (pom.xml, packages.config). Captured: ``XML syntax
#: error on line 1: unexpected EOF``, ``... element <project> closed by
#: </foo>``, ``... invalid character entity &bogus;``, ``... expected element
#: name after <``, ``... attribute name without = in element``, ``... invalid
#: UTF-8``.
_XML_ERROR = rf"XML syntax error on line \d+: {_XML_WORD}(?: {_XML_WORD}){{0,24}}"
#: A bare ``EOF`` (an empty or non-XML pom.xml) or ``unexpected EOF``
#: (conan.lock, renv.lock, packages.lock.json).
_EOF_ERROR = r"(?:unexpected )?EOF"
_PARSER_ERROR = (
    rf"(?:{_JSON_ERROR}|{_YAML_ERROR}|{_TOML_ERROR}|{_XML_ERROR}|{_EOF_ERROR})"
)
#: golang.org/x/mod ``modfile`` (go.mod), always after ``<file>:<line>:``.
#: Captured: ``unknown directive: foo``, ``repeated module statement``,
#: ``unexpected newline in string`` (at ``<file>:1:10``), ``usage: module
#: module/path``, ``usage: require module/path v1.2.3``, ``usage: exclude
#: module/path v1.2.3``, ``invalid go version 'abc': must match format
#: 1.23.0``, ``syntax error (unterminated block started at <file>:3:1)``. The
#: ``replace`` usage spans two lines, so it is kept (read as two errors).
_GOMOD_ERROR = (
    r"(?:usage: (?:module module/path|(?:require|exclude) module/path v1\.2\.3)"
    r"|syntax error \(unterminated block started at \x00:\d+:\d+\)"
    rf"|invalid go version {_QUOTED}: must match format 1\.23\.0"
    rf"|{_PHRASE})"
)
#: The whole text after an excluded file's path that drops its error, with
#: :data:`_SELF` for that path. The wrappers are ``could not extract:`` (most
#: extractors), ``could not extract`` without the colon (``bun.lock``),
#: ``could not extract pom from <file>:`` (``pom.xml``) and ``failed to decode
#: <name> file:`` (``packages.lock.json``); ``packages.config`` prints the XML
#: error bare. An unreadable file prints ``Open(<file>): open <file>:
#: permission denied``.
EXCLUDED_FILE_ERRORS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:could not extract:? |could not extract pom from \x00: "
        rf"|failed to decode {_NAME} file: )?{_PARSER_ERROR}",
    ),
    re.compile(rf"could not extract: \x00:\d+(?::\d+)?: {_GOMOD_ERROR}"),
    re.compile(r"Open\(\x00\): open \x00: permission denied"),
)

#: File names osv-scanner reads, for :func:`_names_another_path`.
_MANIFEST_NAMES: frozenset[str] = frozenset(
    {
        "package-lock.json",
        "npm-shrinkwrap.json",
        "package.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lock",
        "composer.lock",
        "Gemfile.lock",
        "gems.locked",
        "Cargo.lock",
        "go.mod",
        "go.sum",
        "poetry.lock",
        "Pipfile.lock",
        "pdm.lock",
        "uv.lock",
        "pylock.toml",
        "requirements.txt",
        "pom.xml",
        "gradle.lockfile",
        "buildscript-gradle.lockfile",
        "verification-metadata.xml",
        "packages.lock.json",
        "packages.config",
        "deps.json",
        "mix.lock",
        "pubspec.lock",
        "renv.lock",
        "conan.lock",
        "Package.resolved",
        "cabal.project.freeze",
        "stack.yaml.lock",
    },
)
#: Extensions of the files osv-scanner reads, compared case-sensitively.
_MANIFEST_SUFFIXES: tuple[str, ...] = (
    ".json",
    ".lock",
    ".lockfile",
    ".locked",
    ".yaml",
    ".yml",
    ".toml",
    ".xml",
    ".txt",
    ".mod",
    ".sum",
    ".config",
    ".resolved",
    ".freeze",
)
#: ``modfile``'s usage placeholder, which is not a path.
_GOMOD_PLACEHOLDER: str = "module/path"
#: Go-quoted characters (``'/'``, ``'\\x01'``) and back-quoted escape
#: sequences, which may hold a separator or backslash without naming a path.
_ESCAPED_CHAR_RE = re.compile(
    r"'(?:[^'\\\s]|\\(?:[abfnrtv\\']|x[0-9a-f]{2}|u[0-9a-f]{4}|U[0-9a-f]{8}))'"
    r"|`[^`\n]*`",
)
_CANDIDATE_RE = re.compile(r"[^\s'\"()<>\[\]{},;`]+")
_SELF_RE = re.compile(r"\x00(?::\d+)*:?")


def _names_another_path(own: str, printed_path: str) -> bool:
    """Return whether an error names any path besides the excluded file.

    A second guard, independent of :data:`EXCLUDED_FILE_ERRORS`: a word that
    contains a path separator, or is a manifest or lockfile name or ends in
    such an extension, names another path unless it is the file itself
    (written as :data:`_SELF`) or that file's own base name.

    Args:
        own: The error with the file's path written as :data:`_SELF`.
        printed_path: The file's path as osv-scanner printed it.

    Returns:
        True when the error names any other path.
    """
    basename = os.path.basename(printed_path)
    text = _ESCAPED_CHAR_RE.sub(" ", own).replace("</", "<")
    for candidate in _CANDIDATE_RE.findall(text):
        word = _SELF_RE.sub("", candidate).rstrip(".:")
        if not word or word in (basename, _GOMOD_PLACEHOLDER):
            continue
        if (
            "/" in word
            or os.sep in word
            or os.path.basename(word) in _MANIFEST_NAMES
            or word.endswith(_MANIFEST_SUFFIXES)
        ):
            return True
    return False


def is_excluded_file_error(error: str, printed_path: str) -> bool:
    """Return whether an error names only the file it was printed for.

    Args:
        error: The text after the file's path, every line of it.
        printed_path: The file's path as osv-scanner printed it.

    Returns:
        True only when the whole text, with that path written as
        :data:`_SELF`, is one of :data:`EXCLUDED_FILE_ERRORS` and names no
        other path.
    """
    if _SELF in error:
        return False
    own = re.sub(rf"/?{re.escape(printed_path.lstrip('/'))}", _SELF, error)
    if _names_another_path(own, printed_path):
        return False
    return any(pattern.fullmatch(own) for pattern in EXCLUDED_FILE_ERRORS)


def is_harmless_ending(ending: str) -> bool:
    """Return whether a resolution error is a known harmless registry answer.

    Args:
        ending: The message after ``failed resolution for <path>: ``.

    Returns:
        True only when the whole message is one allow-listed shape.
    """
    return any(pattern.fullmatch(ending) for pattern in HARMLESS_RESOLUTION_ENDINGS)


def _read_message(
    lines: list[str],
    start: int,
    match: re.Match[str],
) -> tuple[list[str], int]:
    """Read one ``Error during extraction`` message from its header on.

    Args:
        lines: Every standard-error line.
        start: Index of the header line.
        match: The header match.

    Returns:
        The message's parts (the header's own text, if any, then one per
        continuation), and the index after the message.
    """
    transitive = match.group("plugin") in TRANSITIVE_PLUGINS
    detail = match.group("detail").strip()
    parts = [detail] if detail else []
    # True once the blank line that ends a ``resolution impossible:`` block
    # has been read: only a further ``failed resolution for`` may follow.
    closed = False
    end = start + 1
    while end < len(lines):
        line = lines[end]
        block = bool(parts) and _IMPOSSIBLE in parts[-1].split("\n", 1)[0]
        if closed:
            if not (transitive and line.startswith(_RESOLUTION_PREFIX)):
                break
            parts.append(line)
            closed = False
        elif block and line.startswith(_CAUSE_PREFIXES):
            parts[-1] = f"{parts[-1]}\n{line}"
        elif block and "\n" in parts[-1] and not line.strip():
            closed = True
        elif line.startswith("\t"):
            parts.append(line.strip())
        elif transitive and line.startswith(_RESOLUTION_PREFIX):
            parts.append(line)
        else:
            break
        end += 1
    return parts, end


def read_stderr(
    stderr: str | None,
    scan_root: Path,
    *,
    is_excluded: Callable[[str], bool],
) -> StderrReport:
    """Sort what osv-scanner printed into notes and error text.

    Args:
        stderr: What osv-scanner printed on standard error.
        scan_root: Directory osv-scanner ran in.
        is_excluded: Whether lintro excludes a source path.

    Returns:
        The resolution notes, the error lines, and the number of messages
        dropped because they concern only excluded files.
    """
    lines = [line.rstrip("\r") for line in (stderr or "").splitlines()]
    notes: list[ResolutionNote] = []
    errors: list[str] = []
    excluded = 0
    index = 0
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        match = _HEADER_RE.match(lines[index])
        if match is None:
            errors.append(lines[index])
            index += 1
            continue
        transitive = match.group("plugin") in TRANSITIVE_PLUGINS
        parts, end = _read_message(lines, index, match)
        message_notes: list[ResolutionNote] = []
        all_excluded = bool(parts)
        harmless = bool(parts)
        for part in parts:
            resolution = transitive and part.startswith(_RESOLUTION_PREFIX)
            text = part.removeprefix(_RESOLUTION_PREFIX) if resolution else part
            named = source_of(text, scan_root)
            if (
                named is not None
                and is_excluded(named[0])
                and (
                    is_harmless_ending(named[2])
                    if resolution
                    else is_excluded_file_error(named[2], named[1])
                )
            ):
                continue
            all_excluded = False
            if resolution and named is not None and is_harmless_ending(named[2]):
                message_notes.append(ResolutionNote(named[0], named[1], text))
            else:
                harmless = False
        if all_excluded:
            excluded += 1
        elif harmless:
            notes.extend(message_notes)
        else:
            errors.extend(line for line in lines[index:end] if line.strip())
        index = end
    return StderrReport(
        notes=tuple(notes),
        errors=tuple(errors),
        excluded=excluded,
        printed=tuple(line for line in lines if line.strip()),
    )


def display_path(source: str, scan_root: Path) -> str:
    """Return a source path relative to the scan root when it lies inside it.

    Args:
        source: Absolute path of the source.
        scan_root: Directory osv-scanner ran in.

    Returns:
        The relative path, or ``source`` when it lies outside the scan root.
    """
    try:
        return str(Path(source).resolve().relative_to(scan_root.resolve()))
    except ValueError:
        return source


#: The most manifests a resolution note quotes; the rest are counted.
MAX_NOTE_MANIFESTS: int = 5


def resolution_note(notes: tuple[ResolutionNote, ...], scan_root: Path) -> str:
    """Name the manifests whose indirect dependencies were not resolved.

    Args:
        notes: The resolution notes osv-scanner's errors carried.
        scan_root: Directory osv-scanner ran in.

    Returns:
        The note, or an empty string when there is none. Each manifest's
        message is quoted up to its first lines (joined by `` | ``). With more
        than one manifest each quote is numbered ``[1]``, ``[2]`` and the
        quotes are joined by ``; ``; at most :data:`MAX_NOTE_MANIFESTS` are
        quoted and the rest counted as ``and N more``.
    """
    if not notes:
        return ""
    quotes = [
        tool_message_excerpt(
            note.text.replace(
                note.printed_path,
                display_path(note.source, scan_root),
                1,
            ),
        )
        for note in notes[:MAX_NOTE_MANIFESTS]
    ]
    if len(notes) == 1:
        shown = quotes[0]
    else:
        shown = "; ".join(
            f"[{number}] {quote}" for number, quote in enumerate(quotes, start=1)
        )
        if len(notes) > MAX_NOTE_MANIFESTS:
            shown += f"; and {len(notes) - MAX_NOTE_MANIFESTS} more"
    return (
        "OSV-Scanner scanned the packages listed in these manifests but "
        "could not resolve their indirect dependencies, so those were not "
        f"checked: {shown}"
    )
