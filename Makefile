# Docs gates: see tools/README.md.
# make check                      every gate over the whole repository
# make check SCOPE="guides"        V1-V3, V5, V6 scoped; V4 always whole tree
SCOPE ?=

.PHONY: check
check:
	@tools/check.sh $(SCOPE)
