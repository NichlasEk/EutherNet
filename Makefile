.PHONY: inventory
.PHONY: status
.PHONY: repos
.PHONY: ask
.PHONY: run
.PHONY: mcp
.PHONY: serve

inventory:
	python scripts/euthernet_inventory.py --config euthernet.toml

status:
	python scripts/euthernet_cli.py --config euthernet.toml status

repos:
	python scripts/euthernet_cli.py --config euthernet.toml repos

ask:
	python scripts/euthernet_cli.py --config euthernet.toml ask "$(Q)"

run:
	python scripts/euthernet_cli.py --config euthernet.toml run "$(CMD)"

mcp:
	python scripts/euthernet_mcp.py --config euthernet.toml

serve:
	python scripts/euthernet_http.py --config euthernet.toml
