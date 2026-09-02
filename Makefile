# sadana-harness — the verification vocabulary.
#
# Every target is one command, exits non-zero on failure, and ends in a line
# you can recognise. `make` with no argument prints this menu.
#
# The rule that keeps this readable: a target is one line. The moment it needs
# branching, environment setup or error handling it becomes a script in
# scripts/ and the target calls it. `test` is the only one that has earned a
# script so far.

# Prefer the project venv without anyone having to remember to activate it.
# "Did you activate the venv?" is a failure mode a human eventually learns to
# avoid and an agent never does.
export PATH := $(CURDIR)/.venv/bin:$(PATH)

.DEFAULT_GOAL := help
.PHONY: help chain lint typecheck test verify

help:  ## show this menu
	@grep -E '^[a-z-]+:.*?##' $(MAKEFILE_LIST) \
	  | sed 's/:.*##/\t/' | awk -F'\t' '{printf "  \033[1m%-10s\033[0m %s\n", $$1, $$2}'

chain:  ## the artifact chain is whole            -> CHAIN OK
	@python3 scripts/artifact.py check && echo "CHAIN OK"

lint:  ## style, format and every pre-commit hook -> LINT OK
	@pre-commit run --all-files && echo "LINT OK"

typecheck:  ## static types                       -> TYPES OK
	@mypy src && echo "TYPES OK"

test:  ## the suite, hermetic, CI-identical       -> TESTS OK
	@bash scripts/run_tests.sh

# The single door: what a session runs before reporting done, what the Stop
# hook calls, and what CI will call. One definition of "the tree is good",
# three callers, no drift. Ordered cheapest-first so a broken tree fails in
# seconds, not minutes.
verify: chain lint typecheck test  ## everything, fail-fast -> VERIFY OK
	@echo "VERIFY OK"
