from scripts.check_reference_citations import CITATION_RE, Citation, find_broken_citations


def test_a_docs_console_citation_is_matched_by_the_pattern():
    """H19: the regex used to only see `docs/reference/...` — a citation
    into `docs/console/...` was invisible to it, neither checked nor
    rejected. Now it resolves the same way."""
    assert CITATION_RE.findall("see docs/console/wire.md for the grammar") == ["docs/console/wire.md"]
    assert CITATION_RE.findall("schema: docs/console/grammar.json") == ["docs/console/grammar.json"]


def test_a_tracked_docs_console_citation_is_not_broken():
    citations = [Citation("README.md", 3, "docs/console/capabilities.md")]
    tracked = {"docs/console/capabilities.md"}
    assert find_broken_citations(citations, tracked) == []


def test_an_untracked_docs_console_citation_is_broken():
    # Extension deliberately isn't .md/.json, for the same reason as
    # test_untracked_and_undeclared_citation_is_broken below: this file is
    # itself tracked and scanned by the checker under test.
    citation = Citation("docs/tasks/X/spec.md", 5, "docs/console/nonexistent.invalid")
    assert find_broken_citations([citation], set()) == [citation]


def test_tracked_citation_is_not_broken():
    citations = [Citation("README.md", 3, "docs/reference/plugin_blueprint.md")]
    tracked = {"docs/reference/plugin_blueprint.md"}
    assert find_broken_citations(citations, tracked) == []


def test_allowed_untracked_generated_index_is_not_broken():
    citations = [Citation("CLAUDE.md", 10, "docs/reference/hermes_core_blocks_kind.csv")]
    tracked: set[str] = set()
    assert find_broken_citations(citations, tracked) == []


def test_untracked_and_undeclared_citation_is_broken():
    # Extension deliberately isn't .md/.csv: this file is itself tracked and
    # scanned by the checker under test, and find_broken_citations doesn't
    # care about extensions — only a real prose citation does.
    citation = Citation("docs/tasks/X/spec.md", 5, "docs/reference/some_blueprint.invalid")
    assert find_broken_citations([citation], set()) == [citation]


def test_no_citations_is_a_valid_empty_result():
    assert find_broken_citations([], {"docs/reference/plugin_blueprint.md"}) == []
