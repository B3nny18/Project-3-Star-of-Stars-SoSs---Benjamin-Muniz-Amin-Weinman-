#   make            check that every source file compiles (warnings are errors)
#   make run        run with STARS (default: 3 stars of 3 nodes)
#   make demo       same, but the main CCS fails mid-run and the shadow takes over
#   make inputs     generate random test input files for STARS
#   make clean      remove generated output files and caches
# Override the layout like: make run STARS="4 2 5"

PYTHON  ?= python3
STARS   ?= 3 3 3
FAIL_AT ?= 15

.PHONY: all build run demo inputs clean

all: build

build:
	$(PYTHON) -W error -m py_compile protocol.py frame.py firewall.py switch.py cas.py ccs.py node.py main.py gen_inputs.py
	@echo "Build OK"

run: build
	$(PYTHON) main.py $(STARS)

demo: build
	$(PYTHON) main.py $(STARS) --fail-ccs $(FAIL_AT)

inputs:
	$(PYTHON) gen_inputs.py $(STARS)

clean:
	rm -rf __pycache__ node*_*output.txt
