"""osv-scanner standard error captured from real runs (#3352).

Generated from osv-scanner 2.4.0 and 2.7.1 runs over the manifests in
:data:`CASE_FILES`, each in its own directory under one root, with a
healthy network (``"ok"``) and with the dependency-resolution service
unreachable (``"resolverdown"``: ``HTTPS_PROXY`` on a closed local port,
``NO_PROXY=api.osv.dev,osv.dev`` so vulnerability lookups still work).
The scanned root is written as ``{root}``. In :data:`CAPTURES` the
``End status`` line kept only its directory count when it was recorded; it
is written in its real form with fixed counters and timings.
:data:`CONFIG_CAPTURES` are later runs, kept verbatim: one with the
``.osv-scanner.toml`` in :data:`CONFIG_FILES`, passed with ``--config`` as
lintro does, and one of an empty directory. Replace ``{root}`` with
``str.replace``, not ``str.format``: the Maven lines hold braces.
"""

from __future__ import annotations

from typing import NamedTuple


class Capture(NamedTuple):
    """One real osv-scanner run.

    Attributes:
        version: The osv-scanner version.
        case: The directory scanned, under ``{root}``.
        network: ``"ok"`` or ``"resolverdown"``.
        returncode: osv-scanner's exit status.
        stderr: What it printed on standard error.
    """

    version: str
    case: str
    network: str
    returncode: int
    stderr: str


#: The manifests scanned, relative to ``{root}``.
CASE_FILES: dict[str, str] = {
    "clean/requirements.txt": "six==1.16.0\n",
    "conflict/requirements.txt": "flask==3.0.0\nwerkzeug==0.1\n",
    "impossible/requirements.txt": "flask=====\n",
    "mixed/pom.xml": "<project><modelVersion>4.0.0</modelVersion><groupId>x</groupId><artifactId>y</artifactId><version>1</version><dependencies><dependency><groupId>zz.private</groupId><artifactId>nothere</artifactId><version>1.2.3</version></dependency></dependencies></project>\n",
    "mixed/requirements.txt": "six==1.16.0\nzz-private-internal-pkg-xq==1.2.3\n",
    "mixed/sub/requirements.txt": "flask=====\n",
    "pom/pom.xml": "<project><modelVersion>4.0.0</modelVersion><groupId>x</groupId><artifactId>y</artifactId><version>1</version><dependencies><dependency><groupId>zz.private</groupId><artifactId>nothere</artifactId><version>1.2.3</version></dependency></dependencies></project>\n",
    "private/requirements.txt": "six==1.16.0\nzz-private-internal-pkg-xq==1.2.3\n",
    "privvuln/requirements.txt": "flask==3.0.0\nzz-private-internal-pkg-xq==1.2.3\n",
    "two/a/requirements.txt": "zz-a==1\n",
    "two/b/requirements.txt": "zz-b==1\n",
    "version/requirements.txt": "six==999.0.0\n",
}

#: Every captured run.
CAPTURES: tuple[Capture, ...] = (
    Capture(
        version="2.4.0",
        case="clean",
        network="ok",
        returncode=0,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/clean/requirements.txt file and found 1 package\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
        ),
    ),
    Capture(
        version="2.4.0",
        case="clean",
        network="resolverdown",
        returncode=127,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/clean/requirements.txt file and found 1 package\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            'failed resolution for {root}/clean/requirements.txt: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
            'Error during extraction: (extracting as transitivedependency/requirements) failed resolution for {root}/clean/requirements.txt: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
        ),
    ),
    Capture(
        version="2.4.0",
        case="conflict",
        network="ok",
        returncode=1,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/conflict/requirements.txt file and found 2 packages\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            "failed resolution for {root}/conflict/requirements.txt: resolution impossible:\n"
            'requirements conflict: werkzeug: "==0.1,>=3.0.0"\n'
            "Error during extraction: (extracting as transitivedependency/requirements) failed resolution for {root}/conflict/requirements.txt: resolution impossible:\n"
            'requirements conflict: werkzeug: "==0.1,>=3.0.0"\n'
            "\n"
        ),
    ),
    Capture(
        version="2.4.0",
        case="conflict",
        network="resolverdown",
        returncode=1,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/conflict/requirements.txt file and found 2 packages\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            'failed resolution for {root}/conflict/requirements.txt: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
            'Error during extraction: (extracting as transitivedependency/requirements) failed resolution for {root}/conflict/requirements.txt: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
        ),
    ),
    Capture(
        version="2.4.0",
        case="impossible",
        network="ok",
        returncode=1,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/impossible/requirements.txt file and found 1 package\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            "failed resolution for {root}/impossible/requirements.txt: resolution impossible:\n"
            'no candidates at all for: flask "====="\n'
            "Error during extraction: (extracting as transitivedependency/requirements) failed resolution for {root}/impossible/requirements.txt: resolution impossible:\n"
            'no candidates at all for: flask "====="\n'
            "\n"
        ),
    ),
    Capture(
        version="2.4.0",
        case="impossible",
        network="resolverdown",
        returncode=1,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/impossible/requirements.txt file and found 1 package\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            'failed resolution for {root}/impossible/requirements.txt: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
            'Error during extraction: (extracting as transitivedependency/requirements) failed resolution for {root}/impossible/requirements.txt: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
        ),
    ),
    Capture(
        version="2.4.0",
        case="pom",
        network="ok",
        returncode=127,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/pom/pom.xml file and found 1 package\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            "Warning: enricher transitivedependency/pomxml may be risky when run on untrusted artifacts. Please ensure you trust the source code and artifacts.\n"
            "failed resolution for {root}/pom/pom.xml: failed resolving {Maven:x:y[Concrete:1] {}}: version Maven:zz.private:nothere[Concrete:1.2.3]: not found\n"
            "Error during extraction: (extracting as transitivedependency/pomxml) failed resolution for {root}/pom/pom.xml: failed resolving {Maven:x:y[Concrete:1] {}}: version Maven:zz.private:nothere[Concrete:1.2.3]: not found\n"
        ),
    ),
    Capture(
        version="2.4.0",
        case="pom",
        network="resolverdown",
        returncode=127,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/pom/pom.xml file and found 1 package\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            "Warning: enricher transitivedependency/pomxml may be risky when run on untrusted artifacts. Please ensure you trust the source code and artifacts.\n"
            'failed resolution for {root}/pom/pom.xml: failed resolving {Maven:x:y[Concrete:1] {}}: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
            'Error during extraction: (extracting as transitivedependency/pomxml) failed resolution for {root}/pom/pom.xml: failed resolving {Maven:x:y[Concrete:1] {}}: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
        ),
    ),
    Capture(
        version="2.4.0",
        case="private",
        network="ok",
        returncode=127,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/private/requirements.txt file and found 2 packages\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            "failed resolution for {root}/private/requirements.txt: package System(7):zz-private-internal-pkg-xq: not found\n"
            "Error during extraction: (extracting as transitivedependency/requirements) failed resolution for {root}/private/requirements.txt: package System(7):zz-private-internal-pkg-xq: not found\n"
        ),
    ),
    Capture(
        version="2.4.0",
        case="private",
        network="resolverdown",
        returncode=127,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/private/requirements.txt file and found 2 packages\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            'failed resolution for {root}/private/requirements.txt: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
            'Error during extraction: (extracting as transitivedependency/requirements) failed resolution for {root}/private/requirements.txt: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
        ),
    ),
    Capture(
        version="2.4.0",
        case="privvuln",
        network="ok",
        returncode=1,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/privvuln/requirements.txt file and found 2 packages\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            "failed resolution for {root}/privvuln/requirements.txt: package System(7):zz-private-internal-pkg-xq: not found\n"
            "Error during extraction: (extracting as transitivedependency/requirements) failed resolution for {root}/privvuln/requirements.txt: package System(7):zz-private-internal-pkg-xq: not found\n"
        ),
    ),
    Capture(
        version="2.4.0",
        case="privvuln",
        network="resolverdown",
        returncode=1,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/privvuln/requirements.txt file and found 2 packages\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            'failed resolution for {root}/privvuln/requirements.txt: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
            'Error during extraction: (extracting as transitivedependency/requirements) failed resolution for {root}/privvuln/requirements.txt: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
        ),
    ),
    Capture(
        version="2.4.0",
        case="two",
        network="ok",
        returncode=127,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/two/a/requirements.txt file and found 1 package\n"
            "Scanned /{root}/two/b/requirements.txt file and found 1 package\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            "failed resolution for {root}/two/b/requirements.txt: package System(7):zz-b: not found\n"
            "failed resolution for {root}/two/a/requirements.txt: package System(7):zz-a: not found\n"
            "Error during extraction: (extracting as transitivedependency/requirements) failed resolution for {root}/two/b/requirements.txt: package System(7):zz-b: not found\n"
            "failed resolution for {root}/two/a/requirements.txt: package System(7):zz-a: not found\n"
        ),
    ),
    Capture(
        version="2.4.0",
        case="two",
        network="resolverdown",
        returncode=127,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/two/a/requirements.txt file and found 1 package\n"
            "Scanned /{root}/two/b/requirements.txt file and found 1 package\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            'failed resolution for {root}/two/a/requirements.txt: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
            'failed resolution for {root}/two/b/requirements.txt: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
            'Error during extraction: (extracting as transitivedependency/requirements) failed resolution for {root}/two/a/requirements.txt: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
            'failed resolution for {root}/two/b/requirements.txt: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
        ),
    ),
    Capture(
        version="2.4.0",
        case="version",
        network="ok",
        returncode=127,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/version/requirements.txt file and found 1 package\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            "failed resolution for {root}/version/requirements.txt: resolution impossible:\n"
            'no candidates at all for: six "==999.0.0"\n'
            "Error during extraction: (extracting as transitivedependency/requirements) failed resolution for {root}/version/requirements.txt: resolution impossible:\n"
            'no candidates at all for: six "==999.0.0"\n'
            "\n"
        ),
    ),
    Capture(
        version="2.4.0",
        case="version",
        network="resolverdown",
        returncode=127,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/version/requirements.txt file and found 1 package\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            'failed resolution for {root}/version/requirements.txt: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
            'Error during extraction: (extracting as transitivedependency/requirements) failed resolution for {root}/version/requirements.txt: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
        ),
    ),
    Capture(
        version="2.7.1",
        case="clean",
        network="ok",
        returncode=0,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/clean/requirements.txt file and found 1 package\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
        ),
    ),
    Capture(
        version="2.7.1",
        case="clean",
        network="resolverdown",
        returncode=127,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/clean/requirements.txt file and found 1 package\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            'failed resolution for {root}/clean/requirements.txt: max retries exhausted: failed after 4 attempts: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
            'Error during extraction: (extracting as transitivedependency/requirements) failed resolution for {root}/clean/requirements.txt: max retries exhausted: failed after 4 attempts: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
        ),
    ),
    Capture(
        version="2.7.1",
        case="conflict",
        network="ok",
        returncode=1,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/conflict/requirements.txt file and found 2 packages\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            "failed resolution for {root}/conflict/requirements.txt: resolution impossible:\n"
            'requirements conflict: werkzeug: "==0.1,>=3.0.0"\n'
            "Error during extraction: (extracting as transitivedependency/requirements) failed resolution for {root}/conflict/requirements.txt: resolution impossible:\n"
            'requirements conflict: werkzeug: "==0.1,>=3.0.0"\n'
            "\n"
        ),
    ),
    Capture(
        version="2.7.1",
        case="conflict",
        network="resolverdown",
        returncode=1,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/conflict/requirements.txt file and found 2 packages\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            'failed resolution for {root}/conflict/requirements.txt: max retries exhausted: failed after 4 attempts: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
            'Error during extraction: (extracting as transitivedependency/requirements) failed resolution for {root}/conflict/requirements.txt: max retries exhausted: failed after 4 attempts: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
        ),
    ),
    Capture(
        version="2.7.1",
        case="impossible",
        network="ok",
        returncode=1,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/impossible/requirements.txt file and found 1 package\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            "failed resolution for {root}/impossible/requirements.txt: resolution impossible:\n"
            'no candidates at all for: flask "====="\n'
            "Error during extraction: (extracting as transitivedependency/requirements) failed resolution for {root}/impossible/requirements.txt: resolution impossible:\n"
            'no candidates at all for: flask "====="\n'
            "\n"
        ),
    ),
    Capture(
        version="2.7.1",
        case="impossible",
        network="resolverdown",
        returncode=1,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/impossible/requirements.txt file and found 1 package\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            'failed resolution for {root}/impossible/requirements.txt: max retries exhausted: failed after 4 attempts: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
            'Error during extraction: (extracting as transitivedependency/requirements) failed resolution for {root}/impossible/requirements.txt: max retries exhausted: failed after 4 attempts: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
        ),
    ),
    Capture(
        version="2.7.1",
        case="pom",
        network="ok",
        returncode=127,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/pom/pom.xml file and found 1 package\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            "Warning: enricher transitivedependency/pomxml may be risky when run on untrusted artifacts. Please ensure you trust the source code and artifacts.\n"
            "failed resolution for {root}/pom/pom.xml: failed resolving {Maven:x:y[Concrete:1] {}}: version Maven:zz.private:nothere[Concrete:1.2.3]: not found\n"
            "Error during extraction: (extracting as transitivedependency/pomxml) failed resolution for {root}/pom/pom.xml: failed resolving {Maven:x:y[Concrete:1] {}}: version Maven:zz.private:nothere[Concrete:1.2.3]: not found\n"
        ),
    ),
    Capture(
        version="2.7.1",
        case="pom",
        network="resolverdown",
        returncode=127,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/pom/pom.xml file and found 1 package\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            "Warning: enricher transitivedependency/pomxml may be risky when run on untrusted artifacts. Please ensure you trust the source code and artifacts.\n"
            'failed resolution for {root}/pom/pom.xml: failed resolving {Maven:x:y[Concrete:1] {}}: max retries exhausted: failed after 4 attempts: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
            'Error during extraction: (extracting as transitivedependency/pomxml) failed resolution for {root}/pom/pom.xml: failed resolving {Maven:x:y[Concrete:1] {}}: max retries exhausted: failed after 4 attempts: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
        ),
    ),
    Capture(
        version="2.7.1",
        case="private",
        network="ok",
        returncode=127,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/private/requirements.txt file and found 2 packages\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            "failed resolution for {root}/private/requirements.txt: package System(7):zz-private-internal-pkg-xq: not found\n"
            "Error during extraction: (extracting as transitivedependency/requirements) failed resolution for {root}/private/requirements.txt: package System(7):zz-private-internal-pkg-xq: not found\n"
        ),
    ),
    Capture(
        version="2.7.1",
        case="private",
        network="resolverdown",
        returncode=127,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/private/requirements.txt file and found 2 packages\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            'failed resolution for {root}/private/requirements.txt: max retries exhausted: failed after 4 attempts: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
            'Error during extraction: (extracting as transitivedependency/requirements) failed resolution for {root}/private/requirements.txt: max retries exhausted: failed after 4 attempts: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
        ),
    ),
    Capture(
        version="2.7.1",
        case="privvuln",
        network="ok",
        returncode=1,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/privvuln/requirements.txt file and found 2 packages\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            "failed resolution for {root}/privvuln/requirements.txt: package System(7):zz-private-internal-pkg-xq: not found\n"
            "Error during extraction: (extracting as transitivedependency/requirements) failed resolution for {root}/privvuln/requirements.txt: package System(7):zz-private-internal-pkg-xq: not found\n"
        ),
    ),
    Capture(
        version="2.7.1",
        case="privvuln",
        network="resolverdown",
        returncode=1,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/privvuln/requirements.txt file and found 2 packages\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            'failed resolution for {root}/privvuln/requirements.txt: max retries exhausted: failed after 4 attempts: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
            'Error during extraction: (extracting as transitivedependency/requirements) failed resolution for {root}/privvuln/requirements.txt: max retries exhausted: failed after 4 attempts: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
        ),
    ),
    Capture(
        version="2.7.1",
        case="two",
        network="ok",
        returncode=127,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/two/a/requirements.txt file and found 1 package\n"
            "Scanned /{root}/two/b/requirements.txt file and found 1 package\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            "failed resolution for {root}/two/a/requirements.txt: package System(7):zz-a: not found\n"
            "failed resolution for {root}/two/b/requirements.txt: package System(7):zz-b: not found\n"
            "Error during extraction: (extracting as transitivedependency/requirements) failed resolution for {root}/two/a/requirements.txt: package System(7):zz-a: not found\n"
            "failed resolution for {root}/two/b/requirements.txt: package System(7):zz-b: not found\n"
        ),
    ),
    Capture(
        version="2.7.1",
        case="two",
        network="resolverdown",
        returncode=127,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/two/a/requirements.txt file and found 1 package\n"
            "Scanned /{root}/two/b/requirements.txt file and found 1 package\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            'failed resolution for {root}/two/a/requirements.txt: max retries exhausted: failed after 4 attempts: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
            'failed resolution for {root}/two/b/requirements.txt: max retries exhausted: failed after 4 attempts: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
            'Error during extraction: (extracting as transitivedependency/requirements) failed resolution for {root}/two/a/requirements.txt: max retries exhausted: failed after 4 attempts: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
            'failed resolution for {root}/two/b/requirements.txt: max retries exhausted: failed after 4 attempts: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
        ),
    ),
    Capture(
        version="2.7.1",
        case="version",
        network="ok",
        returncode=127,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/version/requirements.txt file and found 1 package\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            "failed resolution for {root}/version/requirements.txt: resolution impossible:\n"
            'no candidates at all for: six "==999.0.0"\n'
            "Error during extraction: (extracting as transitivedependency/requirements) failed resolution for {root}/version/requirements.txt: resolution impossible:\n"
            'no candidates at all for: six "==999.0.0"\n'
            "\n"
        ),
    ),
    Capture(
        version="2.7.1",
        case="version",
        network="resolverdown",
        returncode=127,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/version/requirements.txt file and found 1 package\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            'failed resolution for {root}/version/requirements.txt: max retries exhausted: failed after 4 attempts: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
            'Error during extraction: (extracting as transitivedependency/requirements) failed resolution for {root}/version/requirements.txt: max retries exhausted: failed after 4 attempts: rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: dial tcp 127.0.0.1:9: connect: connection refused"\n'
        ),
    ),
    Capture(
        version="2.7.1",
        case="mixed",
        network="ok",
        returncode=1,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/mixed/requirements.txt file and found 2 packages\n"
            "Scanned /{root}/mixed/pom.xml file and found 1 package\n"
            "Scanned /{root}/mixed/sub/requirements.txt file and found 1 package\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            "Warning: enricher transitivedependency/pomxml may be risky when run on untrusted artifacts. Please ensure you trust the source code and artifacts.\n"
            "failed resolution for {root}/mixed/pom.xml: failed resolving {Maven:x:y[Concrete:1] {}}: version Maven:zz.private:nothere[Concrete:1.2.3]: not found\n"
            "failed resolution for {root}/mixed/requirements.txt: package System(7):zz-private-internal-pkg-xq: not found\n"
            "failed resolution for {root}/mixed/sub/requirements.txt: resolution impossible:\n"
            'no candidates at all for: flask "====="\n'
            "Error during extraction: (extracting as transitivedependency/pomxml) failed resolution for {root}/mixed/pom.xml: failed resolving {Maven:x:y[Concrete:1] {}}: version Maven:zz.private:nothere[Concrete:1.2.3]: not found\n"
            "Error during extraction: (extracting as transitivedependency/requirements) failed resolution for {root}/mixed/requirements.txt: package System(7):zz-private-internal-pkg-xq: not found\n"
            "failed resolution for {root}/mixed/sub/requirements.txt: resolution impossible:\n"
            'no candidates at all for: flask "====="\n'
            "\n"
        ),
    ),
    Capture(
        version="2.4.0",
        case="mixed",
        network="ok",
        returncode=1,
        stderr=(
            "Scanning dir .\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/mixed/requirements.txt file and found 2 packages\n"
            "Scanned /{root}/mixed/pom.xml file and found 1 package\n"
            "Scanned /{root}/mixed/sub/requirements.txt file and found 1 package\n"
            "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls, 1.5ms elapsed, 1.5ms wall time\n"
            "Warning: enricher transitivedependency/pomxml may be risky when run on untrusted artifacts. Please ensure you trust the source code and artifacts.\n"
            "failed resolution for {root}/mixed/pom.xml: failed resolving {Maven:x:y[Concrete:1] {}}: version Maven:zz.private:nothere[Concrete:1.2.3]: not found\n"
            "failed resolution for {root}/mixed/requirements.txt: package System(7):zz-private-internal-pkg-xq: not found\n"
            "failed resolution for {root}/mixed/sub/requirements.txt: resolution impossible:\n"
            'no candidates at all for: flask "====="\n'
            "Error during extraction: (extracting as transitivedependency/pomxml) failed resolution for {root}/mixed/pom.xml: failed resolving {Maven:x:y[Concrete:1] {}}: version Maven:zz.private:nothere[Concrete:1.2.3]: not found\n"
            "Error during extraction: (extracting as transitivedependency/requirements) failed resolution for {root}/mixed/requirements.txt: package System(7):zz-private-internal-pkg-xq: not found\n"
            "failed resolution for {root}/mixed/sub/requirements.txt: resolution impossible:\n"
            'no candidates at all for: flask "====="\n'
            "\n"
        ),
    ),
)

#: The manifests and configuration of :data:`CONFIG_CAPTURES`, relative to
#: ``{root}``; ``empty`` is a directory with nothing in it.
CONFIG_FILES: dict[str, str] = {
    "cfg/.osv-scanner.toml": '[[IgnoredVulns]]\nid = "PYSEC-2026-2151"\nignoreUntil = 2099-01-01\nreason = "flask session issue accepted: not reachable"\n\n[[IgnoredVulns]]\nid = "PYSEC-2021-66"\nignoreUntil = 2099-01-01\n\n[[IgnoredVulns]]\nid = "PYSEC-2019-132"\nignoreUntil = 2099-01-01\nreason = """first line of the reason\nsecond line: still the reason"""\n\n[[IgnoredVulns]]\nid = "GHSA-zzzz-zzzz-zzzz"\nignoreUntil = 2099-01-01\nreason = "stale entry"\n\n[[IgnoredVulns]]\nid = "PYSEC-2026-2151"\nignoreUntil = 2099-01-01\nreason = "duplicate entry"\n\n[[PackageOverrides]]\nname = "six"\necosystem = "PyPI"\nignore = true\nreason = "vendored copy"\n\n[[PackageOverrides]]\nname = "zz-nothere"\necosystem = "PyPI"\nignore = true\nreason = "unused override"\n',
    "cfg/app/requirements.txt": "flask==3.0.0\njinja2==2.10\nurllib3==1.24.1\nsix==1.16.0\n",
}

#: Runs with a configuration file and of an empty directory, with the
#: scanned directory passed as an absolute path, as lintro does.
CONFIG_CAPTURES: tuple[Capture, ...] = (
    Capture(
        version="2.4.0",
        case="cfg",
        network="ok",
        returncode=1,
        stderr=(
            "warning: /{root}/cfg/.osv-scanner.toml has multiple ignores for PYSEC-2026-2151 - only the first will be used!\n"
            "Scanning dir /{root}/cfg\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/cfg/app/requirements.txt file and found 4 packages\n"
            "End status: 2 dirs visited, 4 inodes visited, 1 Extract calls, 269.125µs elapsed, 269µs wall time\n"
            "failed resolution for {root}/cfg/app/requirements.txt: resolution impossible:\n"
            'requirements conflict: jinja2: "==2.10,>=3.1.2"\n'
            "Error during extraction: (extracting as transitivedependency/requirements) failed resolution for {root}/cfg/app/requirements.txt: resolution impossible:\n"
            'requirements conflict: jinja2: "==2.10,>=3.1.2"\n'
            "\n"
            "Package PyPI/six/1.16.0 has been filtered out because: vendored copy\n"
            "Filtered 1 ignored package/s from the scan.\n"
            "PYSEC-2026-2151 and 2 aliases have been filtered out because: flask session issue accepted: not reachable\n"
            "PYSEC-2021-66 and 3 aliases have been filtered out because: (no reason given)\n"
            "PYSEC-2019-132 and 2 aliases have been filtered out because: first line of the reason\n"
            "second line: still the reason\n"
            "Filtered 6 vulnerabilities from output\n"
            "/{root}/cfg/.osv-scanner.toml has unused ignores:\n"
            " - GHSA-zzzz-zzzz-zzzz\n"
            " - PYSEC-2026-2151\n"
        ),
    ),
    Capture(
        version="2.7.1",
        case="cfg",
        network="ok",
        returncode=1,
        stderr=(
            "warning: /{root}/cfg/.osv-scanner.toml has multiple ignores for PYSEC-2026-2151 - only the first will be used!\n"
            "warning: /{root}/cfg/.osv-scanner.toml has at least one ignore entry using 'ignoreUntil' which has been deprecated in favor of 'effectiveUntil'\n"
            "Scanning dir /{root}/cfg\n"
            "Starting filesystem walk for root: /\n"
            "Scanned /{root}/cfg/app/requirements.txt file and found 4 packages\n"
            "End status: 2 dirs visited, 4 inodes visited, 1 Extract calls, 234.625µs elapsed, 235µs wall time\n"
            "Package PyPI/six/1.16.0 has been filtered out because: vendored copy\n"
            "Filtered 1 ignored package/s from the scan.\n"
            "failed resolution for {root}/cfg/app/requirements.txt: resolution impossible:\n"
            'requirements conflict: jinja2: "==2.10,>=3.1.2"\n'
            "Error during extraction: (extracting as transitivedependency/requirements) failed resolution for {root}/cfg/app/requirements.txt: resolution impossible:\n"
            'requirements conflict: jinja2: "==2.10,>=3.1.2"\n'
            "\n"
            "PYSEC-2026-2151 and 2 aliases have been filtered out because: flask session issue accepted: not reachable\n"
            "PYSEC-2021-66 and 3 aliases have been filtered out because: (no reason given)\n"
            "PYSEC-2019-132 and 2 aliases have been filtered out because: first line of the reason\n"
            "second line: still the reason\n"
            "Filtered 6 vulnerabilities from output\n"
            "/{root}/cfg/.osv-scanner.toml has unused ignores:\n"
            " - GHSA-zzzz-zzzz-zzzz\n"
            " - PYSEC-2026-2151\n"
            "/{root}/cfg/.osv-scanner.toml has unused overrides:\n"
            " - Ecosystem: PyPI, Name: zz-nothere, Reason: unused override\n"
        ),
    ),
    Capture(
        version="2.4.0",
        case="empty",
        network="ok",
        returncode=128,
        stderr=(
            "Scanning dir /{root}/empty\n"
            "Starting filesystem walk for root: /\n"
            "End status: 1 dirs visited, 1 inodes visited, 0 Extract calls, 174.583µs elapsed, 175µs wall time\n"
            "No package sources found, --help for usage information.\n"
        ),
    ),
    Capture(
        version="2.7.1",
        case="empty",
        network="ok",
        returncode=128,
        stderr=(
            "Scanning dir /{root}/empty\n"
            "Starting filesystem walk for root: /\n"
            "End status: 1 dirs visited, 1 inodes visited, 0 Extract calls, 132.792µs elapsed, 133µs wall time\n"
            "No package sources found, --help for usage information.\n"
        ),
    ),
)
