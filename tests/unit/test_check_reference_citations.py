from scripts.check_reference_citations import Citation, find_broken_citations


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
