.PHONY: preflight backup dry-run apply rollback verify help

SHELL := /bin/bash
ROOT  := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
SCRIPTS := $(ROOT)/scripts
STAMP := $(shell date +%Y%m%d-%H%M%S)
DAY   := $(shell date +%Y%m%d)

help:
	@echo "Targets:"
	@echo "  preflight   — read-only health checks (WP REST, FB, IG)"
	@echo "  backup      — snapshot WP pages/posts + FB/IG bios to backups/"
	@echo "  dry-run     — print unified diffs for all planned mutations, no writes"
	@echo "  apply       — execute mutations (requires APPLY=1)"
	@echo "  rollback    — restore from backups/<stamp>/ (STAMP=YYYYMMDD required)"
	@echo "  verify      — run §9 verification checks"

preflight:
	@python3 $(SCRIPTS)/preflight.py

backup:
	@python3 $(SCRIPTS)/backup.py --day $(DAY)

dry-run:
	@python3 $(SCRIPTS)/apply.py --stamp $(STAMP)

apply:
	@test "$$APPLY" = "1" || (echo "Refusing: set APPLY=1 to commit changes"; exit 2)
	@python3 $(SCRIPTS)/apply.py --stamp $(STAMP) --apply

rollback:
	@test -n "$(STAMP)" || (echo "Usage: make rollback STAMP=YYYYMMDD"; exit 2)
	@python3 $(SCRIPTS)/rollback.py --stamp $(STAMP)

verify:
	@python3 $(SCRIPTS)/verify.py
