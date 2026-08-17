VENV   := .venv
PY     := $(VENV)/bin/python
PYTEST := $(VENV)/bin/pytest
N      := 4
IMAGE  := ghcr.io/shiawasenahoshi/telemulator:0.1.0

.DEFAULT_GOAL := help

help:  ## show this list
	@grep -E '^[a-z-]+:.*?##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/'

setup:  ## venv and dependencies
	python3.12 -m venv $(VENV)
	$(VENV)/bin/pip install -e ".[dev]"

test:  ## the whole suite with the coverage gate
	$(PYTEST) --cov=telemulator --cov-branch -n $(N) -q

web:  ## the web client on :8081
	$(VENV)/bin/uvicorn telemulator.app:create_app --factory --host 127.0.0.1 --port 8081

image:  ## build the docker image
	docker build -t $(IMAGE) .
