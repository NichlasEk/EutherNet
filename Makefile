.PHONY: inventory
.PHONY: status
.PHONY: repos
.PHONY: ask
.PHONY: run
.PHONY: mcp
.PHONY: serve
.PHONY: summary
.PHONY: changes
.PHONY: restore-plan
.PHONY: restore-bundle
.PHONY: backup-manifest
.PHONY: restore-drill
.PHONY: server-map

inventory:
	python scripts/euthernet_inventory.py --config euthernet.toml

status:
	python scripts/euthernet_cli.py --config euthernet.toml status

repos:
	python scripts/euthernet_cli.py --config euthernet.toml repos

ask:
	python scripts/euthernet_cli.py --config euthernet.toml ask "$(Q)"

summary:
	python scripts/euthernet_cli.py --config euthernet.toml summary

changes:
	python scripts/euthernet_cli.py --config euthernet.toml changes

restore-plan:
	python scripts/euthernet_cli.py --config euthernet.toml restore-plan

restore-bundle:
	python scripts/euthernet_cli.py --config euthernet.toml restore-bundle --profile "$(PROFILE)"

backup-manifest:
	@python scripts/euthernet_cli.py --config euthernet.toml backup-manifest

restore-drill:
	@python scripts/euthernet_cli.py --config euthernet.toml restore-drill

server-map:
	@python scripts/euthernet_cli.py --config euthernet.toml server-map

run:
	python scripts/euthernet_cli.py --config euthernet.toml run "$(CMD)"

mcp:
	python scripts/euthernet_mcp.py --config euthernet.toml

serve:
	python scripts/euthernet_http.py --config euthernet.toml
